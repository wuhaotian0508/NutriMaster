#!/usr/bin/env python3
"""Memory profiling script for NutriMaster startup and RAG query phases.

Usage:
    # Basic (RSS only, no external calls):
    python tests/profile_memory.py --dry-run

    # Full profile including real RAG queries (requires .env with API keys):
    python tests/profile_memory.py --query "vitamin D deficiency in tomato"

    # Separate PubMed and gene-db phases:
    python tests/profile_memory.py --pubmed-only --query "lycopene biosynthesis"
    python tests/profile_memory.py --genedb-only --query "lycopene biosynthesis"

    # Save JSON report:
    python tests/profile_memory.py --query "..." --json logs/mem_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pickle
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure src/ is on the path when running directly (pytest adds it via config)
# ---------------------------------------------------------------------------
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "src"))

from dotenv import load_dotenv
load_dotenv(_repo_root / ".env")

import numpy as np

# ---------------------------------------------------------------------------
# Lightweight helpers
# ---------------------------------------------------------------------------

try:
    import psutil
    _PROC = psutil.Process()

    def rss_mb() -> float:
        return _PROC.memory_info().rss / 1024 / 1024
except ImportError:
    # Fallback: parse /proc/self/status on Linux
    def rss_mb() -> float:  # type: ignore[misc]
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
        except Exception:
            pass
        return 0.0


def heap_mb() -> tuple[float, float]:
    """Return (current_heap_mb, peak_heap_mb) from tracemalloc; (0, 0) if disabled."""
    if not tracemalloc.is_tracing():
        return 0.0, 0.0
    cur, peak = tracemalloc.get_traced_memory()
    return cur / 1024 / 1024, peak / 1024 / 1024


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Snap:
    label: str
    rss: float
    heap_cur: float
    heap_peak: float
    t: float  # elapsed seconds from profiler start


@dataclass
class Span:
    label: str
    rss_before: float
    rss_after: float
    heap_before: float
    heap_after: float
    heap_peak_in_span: float
    duration: float

    @property
    def rss_delta(self) -> float:
        return self.rss_after - self.rss_before

    @property
    def heap_delta(self) -> float:
        return self.heap_after - self.heap_before


@dataclass
class ProfileReport:
    tracemalloc_enabled: bool
    started_at: str
    snaps: list[Snap] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    top_allocs: list[dict] = field(default_factory=list)

    def add_snap(self, label: str, t0: float) -> None:
        cur, peak = heap_mb()
        self.snaps.append(Snap(label=label, rss=rss_mb(), heap_cur=cur, heap_peak=peak, t=time.perf_counter() - t0))

    def measure_span(self, label: str, t0: float):
        """Context manager that records a Span around a block."""
        return _SpanCtx(label, t0, self)

    def collect_top_allocs(self, top_n: int = 15) -> None:
        if not tracemalloc.is_tracing():
            return
        snapshot = tracemalloc.take_snapshot()
        stats = snapshot.statistics("lineno")
        self.top_allocs = [
            {
                "file": str(s.traceback[0].filename),
                "line": s.traceback[0].lineno,
                "size_mb": round(s.size / 1024 / 1024, 3),
                "count": s.count,
            }
            for s in stats[:top_n]
        ]

    def print(self) -> None:
        _print_report(self)

    def save_json(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tracemalloc_enabled": self.tracemalloc_enabled,
            "started_at": self.started_at,
            "snapshots": [asdict(s) for s in self.snaps],
            "spans": [asdict(s) for s in self.spans],
            "top_allocs": self.top_allocs,
        }
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON report saved: {path}")


class _SpanCtx:
    def __init__(self, label: str, t0: float, report: ProfileReport):
        self._label = label
        self._t0 = t0
        self._report = report

    def __enter__(self):
        tracemalloc.reset_peak() if tracemalloc.is_tracing() else None
        self._rss0 = rss_mb()
        self._heap0, _ = heap_mb()
        self._wall0 = time.perf_counter()
        return self

    def __exit__(self, *_):
        rss1 = rss_mb()
        heap1, peak1 = heap_mb()
        dur = time.perf_counter() - self._wall0
        self._report.spans.append(Span(
            label=self._label,
            rss_before=self._rss0, rss_after=rss1,
            heap_before=self._heap0, heap_after=heap1,
            heap_peak_in_span=peak1,
            duration=dur,
        ))

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *a):
        self.__exit__(*a)


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------

def _fmt(mb: float) -> str:
    return f"{mb:>7.1f} MB"

def _print_report(r: ProfileReport) -> None:
    mode = "RSS + tracemalloc (heap)" if r.tracemalloc_enabled else "RSS only"
    print(f"\n{'='*60}")
    print(f"NutriMaster Memory Profile")
    print(f"{'='*60}")
    print(f"Started : {r.started_at}")
    print(f"Mode    : {mode}")

    if r.snaps:
        print(f"\n{'─'*60}")
        print("Lifecycle Snapshots")
        print(f"{'─'*60}")
        first_rss = r.snaps[0].rss
        for s in r.snaps:
            delta = f"  (Δ{s.rss - first_rss:+.1f})" if s != r.snaps[0] else ""
            heap_info = f"  heap {_fmt(s.heap_cur)}" if r.tracemalloc_enabled else ""
            print(f"  {s.label:<36} RSS {_fmt(s.rss)}{heap_info}  t={s.t:5.1f}s{delta}")

    if r.spans:
        print(f"\n{'─'*60}")
        print("Span Measurements (Δ = change within span)")
        print(f"{'─'*60}")
        for s in r.spans:
            heap_info = f"  Δheap {s.heap_delta:+.1f} MB  peak {_fmt(s.heap_peak_in_span)}" if r.tracemalloc_enabled else ""
            note = "  [mmap: pages loaded on access]" if "mmap" in s.label else ""
            print(f"  {s.label:<36} ΔRSS {s.rss_delta:+7.1f} MB{heap_info}  {s.duration:.2f}s{note}")

    if r.top_allocs:
        print(f"\n{'─'*60}")
        print(f"Top Python Heap Consumers (tracemalloc)")
        print(f"{'─'*60}")
        for a in r.top_allocs:
            fname = a["file"].replace(str(_repo_root) + "/", "")
            print(f"  {a['size_mb']:>7.1f} MB  {a['count']:>6} allocs  {fname}:{a['line']}")

    if r.snaps:
        baseline = r.snaps[-1].rss if r.snaps else 0
        bm25_span = next((s for s in r.spans if "bm25" in s.label), None)
        print(f"\n{'─'*60}")
        print("Summary")
        print(f"{'─'*60}")
        print(f"  Baseline RSS (after startup)   : {_fmt(baseline)}")
        if bm25_span:
            print(f"  BM25 first-load RSS delta      : {bm25_span.rss_delta:+.1f} MB  → total ~{baseline + bm25_span.rss_delta:.0f} MB")
        for s in r.spans:
            if s.label.startswith("query:"):
                print(f"  {s.label:<36}: ΔRSS {s.rss_delta:+.1f} MB  {s.duration:.2f}s")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Phase 1: Startup (load index from disk)
# ---------------------------------------------------------------------------

def profile_startup(report: ProfileReport, t0: float) -> "JinaRetriever":
    """Load settings + JinaRetriever (which loads chunks.pkl and embeddings.npy)."""
    from nutrimaster.config.settings import Settings
    from nutrimaster.rag.jina import JinaRetriever

    report.add_snap("startup:begin", t0)

    with report.measure_span("startup:import+settings", t0):
        settings = Settings.from_env()

    report.add_snap("startup:before_jina_load", t0)

    # Monkey-patch _load_index to measure sub-phases
    _orig_load = JinaRetriever._load_index

    def _patched_load(self_inner):
        import pickle as _pickle
        import numpy as _np

        chunks_file = self_inner.index_path / "chunks.pkl"
        embeddings_file = self_inner.index_path / "embeddings.npy"
        manifest_file = self_inner.index_path / "manifest.json"
        self_inner.load_error = None
        self_inner._bm25 = None
        self_inner._bm25_error = None

        if chunks_file.exists() and embeddings_file.exists():
            try:
                with report.measure_span("startup:chunks_pkl_load", t0):
                    with chunks_file.open("rb") as fh:
                        chunks = _pickle.load(fh)
                mmap_enabled = os.getenv("NUTRIMASTER_RAG_MMAP_EMBEDDINGS", "1").lower() not in {"0", "false", "no", "off"}
                with report.measure_span("startup:embeddings_npy_load", t0):
                    embeddings = _np.load(embeddings_file, mmap_mode="r" if mmap_enabled else None)
            except Exception as exc:
                self_inner.chunks = []
                self_inner.embeddings = None
                self_inner.load_error = f"{type(exc).__name__}: {exc}"
                return
            if len(chunks) != embeddings.shape[0]:
                self_inner.chunks = []
                self_inner.embeddings = None
                self_inner.load_error = f"Index shape mismatch: chunks={len(chunks)} embeddings={embeddings.shape[0]}"
                return
            self_inner.chunks = chunks
            self_inner.embeddings = embeddings
        else:
            self_inner.chunks = []
            self_inner.embeddings = None
        if manifest_file.exists():
            try:
                json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                pass

    JinaRetriever._load_index = _patched_load
    try:
        retriever = JinaRetriever(settings=settings)
    finally:
        JinaRetriever._load_index = _orig_load

    report.add_snap("startup:after_jina_load", t0)
    return retriever


# ---------------------------------------------------------------------------
# Phase 2: RAG query
# ---------------------------------------------------------------------------

async def profile_rag_query(
    retriever: "JinaRetriever",
    report: ProfileReport,
    t0: float,
    query: str,
    *,
    pubmed_only: bool = False,
    genedb_only: bool = False,
) -> None:
    """Run a real RAG search and measure per-source memory impact."""
    from nutrimaster.rag.service import (
        GeneDbSource, PubMedSource, RAGSearchContext, RAGSearchService
    )

    pubmed_source = PubMedSource()
    gene_db_source = GeneDbSource(retriever)

    # ── PubMed phase ──────────────────────────────────────────────────────
    if not genedb_only:
        report.add_snap("query:pubmed:before", t0)
        async with report.measure_span("query:pubmed_search", t0):
            try:
                pubmed_results = await pubmed_source.search(query, top_k=6)
            except Exception as exc:
                pubmed_results = []
                print(f"  [warn] PubMed search failed: {exc}")
        report.add_snap(f"query:pubmed:after ({len(pubmed_results)} results)", t0)

    if pubmed_only:
        return

    # ── Gene DB (vector + BM25) phase ─────────────────────────────────────
    report.add_snap("query:genedb:before", t0)

    # Intercept hybrid_search to isolate the BM25 lazy-load span
    from nutrimaster.rag import jina as _jina_mod
    _orig_hybrid = _jina_mod.JinaRetriever.hybrid_search

    async def _patched_hybrid(self_inner, q, top_k=20, rerank=True, rerank_top_n=50):
        if self_inner._bm25 is None:
            with report.measure_span("query:bm25_lazy_load", t0):
                self_inner._ensure_bm25()
        return await _orig_hybrid(self_inner, q, top_k=top_k, rerank=rerank, rerank_top_n=rerank_top_n)

    _jina_mod.JinaRetriever.hybrid_search = _patched_hybrid
    try:
        async with report.measure_span("query:genedb_hybrid_search", t0):
            try:
                genedb_results = await gene_db_source.search(query, top_k=12)
            except Exception as exc:
                genedb_results = []
                print(f"  [warn] Gene DB search failed: {exc}")
    finally:
        _jina_mod.JinaRetriever.hybrid_search = _orig_hybrid

    report.add_snap(f"query:genedb:after ({len(genedb_results)} results)", t0)


# ---------------------------------------------------------------------------
# Phase 3: index file sizes (informational, no I/O)
# ---------------------------------------------------------------------------

def print_index_sizes(settings) -> None:
    try:
        index_dir = Path(settings.rag.index_dir)
    except Exception:
        return
    print("\nIndex file sizes:")
    for name in ["chunks.pkl", "embeddings.npy", "bm25.pkl", "manifest.json"]:
        p = index_dir / name
        if p.exists():
            mb = p.stat().st_size / 1024 / 1024
            print(f"  {name:<20} {mb:>7.1f} MB")


# ---------------------------------------------------------------------------
# Dry-run: measure index file load without API calls
# ---------------------------------------------------------------------------

def profile_dry_run(report: ProfileReport, t0: float) -> None:
    """Load index files directly without initialising the full service stack."""
    from nutrimaster.config.settings import Settings
    settings = Settings.from_env()
    try:
        index_dir = Path(settings.rag.index_dir)
    except Exception:
        print("[error] Could not read RAG index_dir from settings")
        return

    report.add_snap("dry_run:begin", t0)
    print_index_sizes(settings)

    chunks_file = index_dir / "chunks.pkl"
    embeddings_file = index_dir / "embeddings.npy"
    bm25_file = index_dir / "bm25.pkl"

    if chunks_file.exists():
        with report.measure_span("dry_run:chunks_pkl_load", t0):
            with chunks_file.open("rb") as fh:
                chunks = pickle.load(fh)
        report.add_snap(f"dry_run:after_chunks ({len(chunks)} chunks)", t0)
    else:
        print(f"  [skip] {chunks_file} not found")

    if embeddings_file.exists():
        mmap_enabled = os.getenv("NUTRIMASTER_RAG_MMAP_EMBEDDINGS", "1").lower() not in {"0", "false", "no", "off"}
        with report.measure_span("dry_run:embeddings_npy_mmap" if mmap_enabled else "dry_run:embeddings_npy_load", t0):
            embeddings = np.load(embeddings_file, mmap_mode="r" if mmap_enabled else None)
        report.add_snap(f"dry_run:after_embeddings (shape={embeddings.shape})", t0)
    else:
        print(f"  [skip] {embeddings_file} not found")

    if bm25_file.exists():
        with report.measure_span("dry_run:bm25_pkl_load", t0):
            with bm25_file.open("rb") as fh:
                bm25 = pickle.load(fh)
        n = getattr(bm25, "n_chunks", None) or getattr(bm25, "corpus_size", "?")
        report.add_snap(f"dry_run:after_bm25 (n={n})", t0)
    else:
        print(f"  [skip] {bm25_file} not found")

    report.collect_top_allocs()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="NutriMaster memory profiling script")
    parser.add_argument("--query", default="vitamin D deficiency mechanism in tomato",
                        help="RAG query string to use during profiling")
    parser.add_argument("--dry-run", action="store_true",
                        help="Load index files directly without full service stack or API calls")
    parser.add_argument("--pubmed-only", action="store_true",
                        help="Only run the PubMed query phase (skips gene DB)")
    parser.add_argument("--genedb-only", action="store_true",
                        help="Only run the gene DB query phase (skips PubMed)")
    parser.add_argument("--tracemalloc", action="store_true",
                        help="Enable tracemalloc heap tracking (adds overhead)")
    parser.add_argument("--json", metavar="PATH", default="",
                        help="Save JSON report to this path (e.g. logs/mem.json)")
    args = parser.parse_args()

    if args.tracemalloc:
        tracemalloc.start()

    import datetime
    t0 = time.perf_counter()
    report = ProfileReport(
        tracemalloc_enabled=tracemalloc.is_tracing(),
        started_at=datetime.datetime.now().isoformat(timespec="seconds"),
    )

    print(f"NutriMaster memory profiling")
    print(f"Query : {args.query}")
    print(f"Mode  : {'dry-run' if args.dry_run else 'full'}")
    print(f"Heap  : {'tracemalloc on' if report.tracemalloc_enabled else 'RSS only'}")
    print()

    if args.dry_run:
        profile_dry_run(report, t0)
    else:
        retriever = profile_startup(report, t0)
        asyncio.run(profile_rag_query(
            retriever, report, t0, args.query,
            pubmed_only=args.pubmed_only,
            genedb_only=args.genedb_only,
        ))
        report.collect_top_allocs()

    report.print()
    if args.json:
        report.save_json(args.json)


if __name__ == "__main__":
    main()
