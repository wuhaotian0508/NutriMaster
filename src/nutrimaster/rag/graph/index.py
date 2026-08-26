from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nutrimaster.rag.graph.schema import relationship_evidence, stable_edge_id

INVALID = {"", "-", "na", "n/a", "none", "null", "nan", "unknown", "not available"}
GRAPH_INDEX_VERSION = "local-graph-v2"
DEFAULT_EDGE_BATCH_SIZE = 2048


def clean(value: Any) -> str:
    """清洗抽取字段；NA/unknown 等占位值当作空值。"""
    text = str(value or "").strip()
    return "" if text.lower() in INVALID else text


def norm(value: Any) -> str:
    """标准化名称，用于搜索和稳定 ID。"""
    return re.sub(r"\s+", " ", clean(value)).lower()


def split_items(value: Any) -> list[str]:
    """把 'CHS; DFR; ANS' 这类字段拆成多个实体。"""
    if not clean(value):
        return []
    raw_items = value if isinstance(value, list) else re.split(r"[;,\n]", str(value))
    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = clean(item)
        key = norm(text)
        if not text or key in seen:
            continue
        seen.add(key)
        items.append(text)
    return items


def species_of(gene: dict[str, Any]) -> str:
    """Gene 节点必须尽量带 species，避免 PAL/CHS/DFR 跨物种误合并。"""
    return (
        clean(gene.get("Species_Latin_Name"))
        or clean(gene.get("Source_Species_Latin_Name"))
        or clean(gene.get("Applied_Species"))
        or clean(gene.get("Species"))
    )


def node_id(node_type: str, name: str, species: str = "") -> str:
    """节点去重 ID：Gene 使用 type+name+species；非 Gene 用 type+name。"""
    species_key = norm(species) if node_type == "Gene" else ""
    raw = f"{node_type}|{norm(name)}|{species_key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def edge_id(src: str, dst: str, relation: str, evidence: dict[str, Any]) -> str:
    """边去重 ID：整体图里同一 src/relation/dst 只保留一条邻接边。"""
    return stable_edge_id(src, dst, relation, evidence)


def _evidence_payload(evidence: dict[str, Any]) -> dict[str, Any]:
    payload = dict(evidence)
    payload["evidence_count"] = 1
    payload["source_sections"] = [evidence["section"]] if evidence.get("section") else []
    return payload


def _merge_evidence_payload(existing_json: str, evidence: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(existing_json)
    except json.JSONDecodeError:
        payload = {}
    return _merge_evidence_payload_dict(payload, evidence)


def _merge_evidence_payload_dict(payload: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload or {})
    for key, value in evidence.items():
        if value and not merged.get(key):
            merged[key] = value
    merged["evidence_count"] = int(merged.get("evidence_count") or 1) + 1
    sections = set(merged.get("source_sections") or [])
    if evidence.get("section"):
        sections.add(evidence["section"])
    merged["source_sections"] = sorted(sections)
    return merged


def _merge_batched_evidence_payload(
    existing_json: str,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge two already-aggregated evidence payloads without losing counts.

    The in-memory edge cache is deliberately bounded, so the same semantic
    edge can be encountered again after an earlier batch has been flushed to
    SQLite.  Combining the two aggregates must be equivalent to feeding every
    observation through ``_merge_evidence_payload_dict`` in corpus order.
    """

    try:
        existing = json.loads(existing_json)
    except json.JSONDecodeError:
        existing = {}
    if not isinstance(existing, dict):
        existing = {}

    merged = dict(existing)
    for key, value in incoming.items():
        if key in {"evidence_count", "source_sections"}:
            continue
        if value and not merged.get(key):
            merged[key] = value

    merged["evidence_count"] = (
        int(existing.get("evidence_count") or 1)
        + int(incoming.get("evidence_count") or 1)
    )
    sections = set(existing.get("source_sections") or [])
    sections.update(incoming.get("source_sections") or [])
    if incoming.get("section"):
        sections.add(incoming["section"])
    merged["source_sections"] = sorted(sections)
    return merged


@dataclass(frozen=True)
class GraphSearchResult:
    seeds: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class LocalGraphIndex:
    """本地 SQLite 图索引：负责建图、去重、解析主体、取局部邻域。"""

    def __init__(
        self,
        db_path: Path | str,
        *,
        edge_batch_size: int = DEFAULT_EDGE_BATCH_SIZE,
    ):
        self.db_path = Path(db_path)
        if not isinstance(edge_batch_size, int) or isinstance(edge_batch_size, bool):
            raise ValueError("edge_batch_size must be a positive integer")
        if edge_batch_size <= 0:
            raise ValueError("edge_batch_size must be a positive integer")
        self.edge_batch_size = edge_batch_size

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            uri = f"{self.db_path.resolve().as_uri()}?mode=ro&immutable=1"
            db = sqlite3.connect(uri, uri=True)
            db.execute("PRAGMA query_only=ON")
        else:
            db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def initialize(self) -> None:
        """重建图索引；图可以从 corpus 再生成，所以直接清空更简单。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()
        with self.connect() as db:
            db.executescript("""
            DROP TABLE IF EXISTS edges;
            DROP TABLE IF EXISTS nodes;
            DROP TABLE IF EXISTS metadata;

            CREATE TABLE metadata(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE nodes(
              id TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              name TEXT NOT NULL,
              norm_name TEXT NOT NULL,
              species TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE edges(
              id TEXT PRIMARY KEY,
              src TEXT NOT NULL,
              dst TEXT NOT NULL,
              relation TEXT NOT NULL,
              evidence_json TEXT NOT NULL
            );

            CREATE INDEX idx_graph_nodes_norm ON nodes(norm_name);
            CREATE INDEX idx_graph_nodes_type_norm ON nodes(type, norm_name);
            CREATE INDEX idx_graph_edges_src ON edges(src);
            CREATE INDEX idx_graph_edges_dst ON edges(dst);
            """)

    def build_from_corpus(
        self,
        corpus_dir: Path | str,
        *,
        corpus_fingerprint: str | None = None,
    ) -> None:
        """从 data/corpus/*.json 建图，并绑定对应的 retrieval corpus。"""
        self.initialize()
        with self.connect() as db:
            self._pending_edges: dict[str, dict[str, Any]] = {}
            try:
                corpus_files = 0
                for path in sorted(Path(corpus_dir).glob("*.json")):
                    try:
                        paper = json.loads(path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        continue
                    corpus_files += 1

                    base = {
                        "source_file": str(path),
                        "title": clean(paper.get("Title")),
                        "journal": clean(paper.get("Journal")),
                        "doi": clean(paper.get("DOI")),
                    }

                    for i, gene in enumerate(paper.get("Pathway_Genes") or []):
                        self._add_pathway_gene(db, gene, base | {"section": "Pathway_Genes", "record_index": i})
                    for i, gene in enumerate(paper.get("Regulation_Genes") or []):
                        self._add_regulation_gene(db, gene, base | {"section": "Regulation_Genes", "record_index": i})
                    for i, gene in enumerate(paper.get("Common_Genes") or []):
                        self._add_common_gene(db, gene, base | {"section": "Common_Genes", "record_index": i})
                self._flush_pending_edges(db)
            finally:
                # Do not retain corpus-derived evidence after a successful or
                # failed build on a long-lived LocalGraphIndex instance.
                self._pending_edges = {}
            db.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (
                    ("version", GRAPH_INDEX_VERSION),
                    ("corpus_fingerprint", corpus_fingerprint or ""),
                    ("corpus_files", str(corpus_files)),
                ),
            )

    def _add_node(self, db: sqlite3.Connection, node_type: str, name: str, species: str = "") -> str:
        """插入节点；INSERT OR IGNORE 实现节点去重。"""
        name = clean(name)
        if not name:
            return ""
        nid = node_id(node_type, name, species)
        db.execute(
            "INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?, ?)",
            (nid, node_type, name, norm(name), clean(species)),
        )
        return nid

    def _add_edge(
        self,
        db: sqlite3.Connection,
        src: str,
        dst: str,
        relation: str,
        evidence: dict[str, Any],
        *,
        field: str,
        item: str = "",
        item_index: int = 0,
    ) -> None:
        """插入边；每条边都带 evidence_json，方便回答时引用来源。"""
        if not src or not dst:
            return
        edge_evidence = relationship_evidence(
            evidence,
            relation=relation,
            field=field,
            item=item,
            item_index=item_index,
        )
        eid = edge_id(src, dst, relation, edge_evidence)
        pending_edges = getattr(self, "_pending_edges", None)
        if pending_edges is not None:
            existing = pending_edges.get(eid)
            if existing:
                existing["evidence"] = _merge_evidence_payload_dict(existing["evidence"], edge_evidence)
                return
            pending_edges[eid] = {
                "id": eid,
                "src": src,
                "dst": dst,
                "relation": relation,
                "evidence": _evidence_payload(edge_evidence),
            }
            if len(pending_edges) >= self.edge_batch_size:
                self._flush_pending_edges(db)
            return

        existing = db.execute("SELECT evidence_json FROM edges WHERE id = ?", (eid,)).fetchone()
        if existing:
            payload = _merge_evidence_payload(existing["evidence_json"], edge_evidence)
            db.execute("UPDATE edges SET evidence_json = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), eid))
            return
        db.execute(
            "INSERT INTO edges VALUES (?, ?, ?, ?, ?)",
            (eid, src, dst, relation, json.dumps(_evidence_payload(edge_evidence), ensure_ascii=False)),
        )

    def _flush_pending_edges(self, db: sqlite3.Connection) -> None:
        """Flush the bounded edge cache and merge cross-batch duplicates."""

        pending_edges = getattr(self, "_pending_edges", None)
        if not pending_edges:
            return
        for edge in pending_edges.values():
            evidence_json = json.dumps(edge["evidence"], ensure_ascii=False)
            inserted = db.execute(
                "INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)",
                (
                    edge["id"],
                    edge["src"],
                    edge["dst"],
                    edge["relation"],
                    evidence_json,
                ),
            )
            if inserted.rowcount:
                continue
            existing = db.execute(
                "SELECT evidence_json FROM edges WHERE id = ?",
                (edge["id"],),
            ).fetchone()
            if existing is None:
                raise RuntimeError("graph edge disappeared during batch merge")
            payload = _merge_batched_evidence_payload(
                existing["evidence_json"],
                edge["evidence"],
            )
            db.execute(
                "UPDATE edges SET evidence_json = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), edge["id"]),
            )
        pending_edges.clear()

    def _gene_node(self, db: sqlite3.Connection, gene: dict[str, Any]) -> str:
        return self._add_node(db, "Gene", clean(gene.get("Gene_Name")), species_of(gene))

    def _add_pathway_gene(self, db: sqlite3.Connection, gene: dict[str, Any], evidence: dict[str, Any]) -> None:
        """Pathway_Genes: 底物 -> Reaction <- Gene -> Reaction -> 产物。"""
        g = self._gene_node(db, gene)
        if not g:
            return

        evidence = evidence | {
            "species": species_of(gene),
            "validation": clean(gene.get("Core_Validation_Method")),
            "summary": clean(gene.get("Summary_Key_Findings_of_Core_Gene")),
        }

        substrates = split_items(gene.get("Primary_Substrate"))
        products = split_items(gene.get("Primary_Product"))
        for index, substrate in enumerate(substrates):
            self._add_edge(
                db,
                self._add_node(db, "Metabolite", substrate),
                g,
                "input_of",
                evidence,
                field="Primary_Substrate",
                item=substrate,
                item_index=index,
            )
        for index, product in enumerate(products):
            self._add_edge(
                db,
                g,
                self._add_node(db, "Metabolite", product),
                "produces",
                evidence,
                field="Primary_Product",
                item=product,
                item_index=index,
            )

        for field, relation, node_type in (
            ("Terminal_Metabolite", "contributes_to", "Metabolite"),
            ("Biosynthetic_Pathway", "participates_in", "Pathway"),
            ("Applied_Species", "tested_in", "Species"),
        ):
            value = clean(gene.get(field))
            if value:
                self._add_edge(db, g, self._add_node(db, node_type, value), relation, evidence, field=field, item=value)

    def _add_regulation_gene(self, db: sqlite3.Connection, gene: dict[str, Any], evidence: dict[str, Any]) -> None:
        """Regulation_Genes: regulator -> target / signal -> regulator。"""
        g = self._gene_node(db, gene)
        if not g:
            return

        species = species_of(gene)
        evidence = evidence | {
            "species": species,
            "mode": clean(gene.get("Regulation_Mode")),
            "validation": clean(gene.get("Core_Validation_Method")),
            "summary": clean(gene.get("Summary_Key_Findings_of_Core_Gene")),
        }

        for index, target in enumerate(split_items(gene.get("Primary_Regulatory_Targets"))):
            self._add_edge(
                db,
                g,
                self._add_node(db, "Gene", target, species),
                "regulates",
                evidence,
                field="Primary_Regulatory_Targets",
                item=target,
                item_index=index,
            )
        for index, signal in enumerate(split_items(gene.get("Upstream_Signals_or_Inputs"))):
            self._add_edge(
                db,
                self._add_node(db, "Signal", signal),
                g,
                "upstream_signal_of",
                evidence,
                field="Upstream_Signals_or_Inputs",
                item=signal,
                item_index=index,
            )

        process = clean(gene.get("Metabolic_Process_Controlled"))
        if process:
            self._add_edge(
                db,
                g,
                self._add_node(db, "Process", process),
                "controls",
                evidence,
                field="Metabolic_Process_Controlled",
                item=process,
            )
        for index, terminal in enumerate(split_items(gene.get("Terminal_Metabolite"))):
            self._add_edge(
                db,
                g,
                self._add_node(db, "Metabolite", terminal),
                "affects",
                evidence,
                field="Terminal_Metabolite",
                item=terminal,
                item_index=index,
            )

    def _add_common_gene(self, db: sqlite3.Connection, gene: dict[str, Any], evidence: dict[str, Any]) -> None:
        """Common_Genes: 更宽泛的 gene-metabolite/phenotype/species 关系。"""
        g = self._gene_node(db, gene)
        if not g:
            return

        evidence = evidence | {
            "species": species_of(gene),
            "validation": clean(gene.get("Core_Validation_Method")),
            "summary": clean(gene.get("Summary_Key_Findings_of_Core_Gene")),
        }

        for field, relation, node_type in (
            ("Terminal_Metabolite", "associated_with", "Metabolite"),
            ("Core_Phenotypic_Effect", "has_effect", "Phenotype"),
            ("Applied_Species", "tested_in", "Species"),
        ):
            value = clean(gene.get(field))
            if value:
                self._add_edge(db, g, self._add_node(db, node_type, value), relation, evidence, field=field, item=value)

    def resolve_seeds(self, query: str, *, species: str = "", limit: int = 12) -> list[dict[str, Any]]:
        """从用户问题里确定图搜索主体；先 exact match，再模糊 match。"""
        tokens = [query, *re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,32}", query)]
        values = []
        for token in tokens:
            value = norm(token)
            if value and value not in values:
                values.append(value)

        if not values or not self.db_path.exists():
            return []

        with self.connect(read_only=True) as db:
            marks = ",".join("?" for _ in values)
            rows = db.execute(
                f"SELECT * FROM nodes WHERE norm_name IN ({marks}) ORDER BY type='Gene' DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
            if not rows:
                rows = db.execute(
                    "SELECT * FROM nodes WHERE norm_name LIKE ? ORDER BY type='Gene' DESC LIMIT ?",
                    (f"%{values[0]}%", limit),
                ).fetchall()

        result = [dict(row) for row in rows]
        if species:
            s = norm(species)
            result = [row for row in result if not row.get("species") or s in norm(row.get("species"))]
        return result

    def neighborhood(
        self,
        query: str,
        *,
        species: str = "",
        hops: int = 1,
        direction: str = "both",
        limit: int = 50,
    ) -> GraphSearchResult:
        """只取主体附近 1-2 跳，不把整张图塞给 LLM。"""
        seeds = self.resolve_seeds(query, species=species)
        if not seeds:
            return GraphSearchResult([], [], [])

        seen = {seed["id"] for seed in seeds}
        frontier = set(seen)
        edges: list[dict[str, Any]] = []

        with self.connect(read_only=True) as db:
            for _ in range(max(1, hops)):
                if not frontier or len(edges) >= limit:
                    break

                clauses, params = [], []
                marks = ",".join("?" for _ in frontier)
                if direction in {"both", "downstream"}:
                    clauses.append(f"src IN ({marks})")
                    params.extend(frontier)
                if direction in {"both", "upstream"}:
                    clauses.append(f"dst IN ({marks})")
                    params.extend(frontier)

                rows = db.execute(
                    f"SELECT * FROM edges WHERE {' OR '.join(clauses)} LIMIT ?",
                    (*params, limit - len(edges)),
                ).fetchall()

                nxt = set()
                for row in rows:
                    edge = dict(row)
                    edge["evidence"] = json.loads(edge.pop("evidence_json"))
                    edges.append(edge)
                    for nid in (edge["src"], edge["dst"]):
                        if nid not in seen:
                            seen.add(nid)
                            nxt.add(nid)
                frontier = nxt

            marks = ",".join("?" for _ in seen)
            nodes = [dict(row) for row in db.execute(f"SELECT * FROM nodes WHERE id IN ({marks})", tuple(seen))]

        return GraphSearchResult(seeds, nodes, edges)
