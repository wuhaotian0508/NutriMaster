from __future__ import annotations

import os
import pickle
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

BM25_INDEX_VERSION = "v4-sparse-bm25"
BM25_K1 = 1.5
BM25_B = 0.75
BM25_EPSILON = 0.25

try:
    import jieba

    jieba.initialize()
    _HAS_JIEBA = True
except MemoryError:
    raise
except Exception:
    _HAS_JIEBA = False


_ASCII_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/]*")
_CN_CHARS = re.compile(r"[\u4e00-\u9fff]+")
_GREEK_COMPOUND = re.compile(r"[\u03b1-\u03c9][\-\u2010-\u2015][A-Za-z0-9\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English scientific text for BM25 retrieval."""
    if not text:
        return []
    text = unicodedata.normalize("NFKC", text)

    tokens: list[str] = []
    tokens.extend(match.group(0).lower() for match in _ASCII_TOKEN.finditer(text))
    tokens.extend(match.group(0).lower() for match in _GREEK_COMPOUND.finditer(text))

    for segment in _CN_CHARS.findall(text):
        if _HAS_JIEBA:
            tokens.extend(word.strip() for word in jieba.lcut_for_search(segment) if word.strip())
        else:
            tokens.extend(segment[index:index + 2] for index in range(max(len(segment) - 1, 0)))

    result = []
    previous = None
    for token in tokens:
        token = token.strip().lower()
        if not token or token == previous:
            continue
        if all(char in ".-_/" for char in token):
            continue
        result.append(token)
        previous = token
    return result


def chunk_to_bm25_text(chunk: Any) -> str:
    """Build a keyword-rich text representation for sparse retrieval."""
    parts = [
        getattr(chunk, "gene_name", ""),
        getattr(chunk, "gene_type", ""),
        getattr(chunk, "chunk_type", ""),
        getattr(chunk, "paper_title", ""),
        getattr(chunk, "journal", ""),
        getattr(chunk, "doi", ""),
        getattr(chunk, "content", ""),
    ]
    metadata = getattr(chunk, "metadata", None)
    if isinstance(metadata, dict):
        for value in metadata.values():
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, (int, float)):
                parts.append(str(value))
            elif isinstance(value, list):
                parts.extend(str(item) for item in value if isinstance(item, str | int | float))
    return "\n".join(part for part in parts if part)


class BM25Retriever:
    """Exact BM25 scoring backed by a compact scipy CSR matrix.

    The former ``rank_bm25`` representation retained one Python dictionary per
    document plus a second token list.  At production scale those Python
    objects expanded a ~600 MB pickle into more than 4 GB of heap.  This
    implementation computes the same BM25Okapi document weights once and keeps
    only numeric sparse arrays and the vocabulary in memory.

    A versioned filename is deliberate: an old object-heavy pickle must never
    be unpickled merely to discover that its embedded version is stale.
    """

    def __init__(self, index_path: Path):
        self.index_path = Path(index_path)
        self.score_matrix = None  # scipy.sparse.csr_matrix | None
        self.term_frequencies: np.ndarray | None = None
        self.vocab: dict[str, int] = {}
        self.n_chunks = 0
        self.corpus_fingerprint: str | None = None
        self.version = BM25_INDEX_VERSION

    @property
    def path(self) -> Path:
        return self.index_path / "bm25_sparse_v4.pkl"

    def build(self, chunks: Sequence[Any], *, corpus_fingerprint: str | None = None) -> None:
        from scipy.sparse import csr_matrix
        from sklearn.feature_extraction.text import CountVectorizer

        self.n_chunks = len(chunks)
        self.corpus_fingerprint = corpus_fingerprint
        self.version = BM25_INDEX_VERSION
        if not chunks:
            self.score_matrix = csr_matrix((0, 0), dtype=np.float32)
            self.term_frequencies = np.zeros(0, dtype=np.uint16)
            self.vocab = {}
            return

        vectorizer = CountVectorizer(analyzer=tokenize, dtype=np.int32)
        # A generator avoids retaining a second corpus-sized list of rendered
        # strings while the already-loaded GeneChunk list is still resident.
        counts = vectorizer.fit_transform(chunk_to_bm25_text(chunk) for chunk in chunks).tocsr()
        self.vocab = vectorizer.vocabulary_

        doc_lengths = np.asarray(counts.sum(axis=1)).ravel().astype(np.float32)
        avg_doc_length = float(doc_lengths.mean()) if len(doc_lengths) else 0.0
        document_frequency = np.bincount(counts.indices, minlength=counts.shape[1]).astype(np.float64)
        idf = np.log(self.n_chunks - document_frequency + 0.5) - np.log(document_frequency + 0.5)
        average_idf = float(idf.mean()) if len(idf) else 0.0
        idf[idf < 0] = BM25_EPSILON * average_idf
        idf = idf.astype(np.float32)

        raw_tf = counts.data
        # Preserve the legacy fallback exactly. Most corpora fit in uint16,
        # but clipping an unusually repetitive document would change its rank.
        max_tf = int(raw_tf.max()) if raw_tf.size else 0
        tf_dtype = np.uint16 if max_tf <= np.iinfo(np.uint16).max else np.uint32
        self.term_frequencies = raw_tf.astype(tf_dtype, copy=True)
        weights = raw_tf.astype(np.float32)
        for row in range(self.n_chunks):
            start = int(counts.indptr[row])
            end = int(counts.indptr[row + 1])
            if start == end:
                continue
            tf = weights[start:end]
            length_norm = BM25_K1 * (
                1.0 - BM25_B
                + BM25_B * (float(doc_lengths[row]) / avg_doc_length if avg_doc_length else 0.0)
            )
            weights[start:end] = (
                idf[counts.indices[start:end]]
                * (tf * (BM25_K1 + 1.0) / (tf + length_norm))
            )

        self.score_matrix = csr_matrix(
            (weights, counts.indices, counts.indptr),
            shape=counts.shape,
            dtype=np.float32,
            copy=False,
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.{id(self)}.tmp")
        tmp_path.unlink(missing_ok=True)
        try:
            with tmp_path.open("wb") as file:
                pickle.dump(
                    {
                        "version": self.version,
                        "score_matrix": self.score_matrix,
                        "term_frequencies": self.term_frequencies,
                        "vocab": self.vocab,
                        "n_chunks": self.n_chunks,
                        "corpus_fingerprint": self.corpus_fingerprint,
                    },
                    file,
                    protocol=4,
                )
                file.flush()
                os.fsync(file.fileno())
            tmp_path.replace(self.path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def load(
        self,
        *,
        expected_chunks: int | None = None,
        expected_fingerprint: str | None = None,
    ) -> bool:
        if not self.path.exists():
            return False
        with self.path.open("rb") as file:
            data = pickle.load(file)
        if data.get("version") != BM25_INDEX_VERSION:
            return False
        n_chunks = int(data.get("n_chunks", 0))
        if expected_chunks is not None and n_chunks != expected_chunks:
            return False
        corpus_fingerprint = data.get("corpus_fingerprint")
        if expected_fingerprint is not None and corpus_fingerprint != expected_fingerprint:
            return False
        score_matrix = data.get("score_matrix")
        term_frequencies = data.get("term_frequencies")
        vocab = data.get("vocab")
        if score_matrix is None or term_frequencies is None or not isinstance(vocab, dict):
            return False
        if score_matrix.shape[0] != n_chunks or score_matrix.data.shape != term_frequencies.shape:
            return False
        self.score_matrix = score_matrix
        self.term_frequencies = term_frequencies
        self.vocab = vocab
        self.n_chunks = n_chunks
        self.corpus_fingerprint = corpus_fingerprint
        self.version = data["version"]
        return True

    def search(self, query: str, top_k: int = 50) -> list[tuple[int, float]]:
        if self.score_matrix is None or self.n_chunks == 0:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        query_counts = Counter(query_tokens)
        query_vector = np.zeros(self.score_matrix.shape[1], dtype=np.float32)
        for token, count in query_counts.items():
            index = self.vocab.get(token)
            if index is not None:
                # BM25Okapi loops over every query token, so duplicate query
                # terms contribute their count rather than a TF-IDF weight.
                query_vector[index] = float(count)
        if not np.any(query_vector):
            return []

        scores = np.asarray(self.score_matrix @ query_vector).ravel()
        if not np.any(scores > 0):
            # Preserve the legacy fallback for corpora where all matching IDFs
            # are non-positive.  The raw TF data shares CSR indices/indptr with
            # the score matrix and costs only two bytes per non-zero entry.
            from scipy.sparse import csr_matrix

            term_frequencies = csr_matrix(
                (
                    self.term_frequencies,
                    self.score_matrix.indices,
                    self.score_matrix.indptr,
                ),
                shape=self.score_matrix.shape,
                copy=False,
            )
            # Use a floating accumulator so large term totals cannot wrap in
            # unsigned arithmetic.
            query_presence = (query_vector > 0).astype(np.float64)
            scores = np.asarray(term_frequencies @ query_presence).ravel()
        order = np.argsort(scores)[::-1][:top_k]
        return [(int(index), float(scores[index])) for index in order if scores[index] > 0]


def rrf_fuse(
    *ranked_lists: Sequence[tuple[int, float]],
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[tuple[int, float]]:
    """Fuse ranked index lists using Reciprocal Rank Fusion."""
    if not ranked_lists:
        return []
    weights = weights or [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights must match ranked_lists")

    fused: dict[int, float] = {}
    for weight, ranked in zip(weights, ranked_lists, strict=True):
        for rank, (index, _score) in enumerate(ranked, start=1):
            fused[index] = fused.get(index, 0.0) + weight / (k + rank)
    return sorted(fused.items(), key=lambda item: item[1], reverse=True)
