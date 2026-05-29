from __future__ import annotations

import pickle
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

BM25_INDEX_VERSION = "v3"

try:
    import jieba

    jieba.initialize()
    _HAS_JIEBA = True
except Exception:
    _HAS_JIEBA = False


_ASCII_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/]*")
_CN_CHARS = re.compile(r"[一-鿿]+")
_GREEK_COMPOUND = re.compile(r"[α-ω][\-‐-―][A-Za-z0-9一-鿿]+")


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


def _build_query_vector(
    tokens: list[str],
    vocab: dict[str, int],
    idf_vec: np.ndarray,
) -> np.ndarray | None:
    """Build an l2-normalised sublinear TF-IDF query vector."""
    counts = Counter(tokens)
    indices, values = [], []
    for token, cnt in counts.items():
        idx = vocab.get(token)
        if idx is not None:
            tf = 1.0 + np.log(float(cnt))
            indices.append(idx)
            values.append(tf * idf_vec[idx])
    if not indices:
        return None
    q = np.zeros(len(vocab), dtype=np.float32)
    q[indices] = values
    norm = np.linalg.norm(q)
    if norm == 0:
        return None
    return q / norm


class BM25Retriever:
    """Sparse TF-IDF retriever backed by a scipy CSR matrix.

    Replaces rank_bm25.BM25Okapi to reduce steady-state RSS from ~4 GB to
    ~150-200 MB for 103k documents.  The external interface (build/save/load/
    search) is unchanged; callers in jina.py require no modification.
    """

    def __init__(self, index_path: Path):
        self.index_path = Path(index_path)
        self.tf_matrix = None   # scipy.sparse.csr_matrix | None
        self.vocab: dict[str, int] = {}
        self.idf_vec: np.ndarray | None = None
        self.n_chunks = 0
        self.version = BM25_INDEX_VERSION

    @property
    def path(self) -> Path:
        return self.index_path / "bm25.pkl"

    def build(self, chunks: Sequence[Any]) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        texts = [chunk_to_bm25_text(c) for c in chunks]
        vec = TfidfVectorizer(analyzer=tokenize, sublinear_tf=True, dtype=np.float32)
        self.tf_matrix = vec.fit_transform(texts)
        self.vocab = vec.vocabulary_
        self.idf_vec = vec.idf_.astype(np.float32)
        self.n_chunks = len(chunks)
        self.version = BM25_INDEX_VERSION

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".pkl.tmp")
        with tmp_path.open("wb") as file:
            pickle.dump(
                {
                    "version": self.version,
                    "tf_matrix": self.tf_matrix,
                    "vocab": self.vocab,
                    "idf_vec": self.idf_vec,
                    "n_chunks": self.n_chunks,
                },
                file,
                protocol=4,
            )
        tmp_path.replace(self.path)

    def load(self, *, expected_chunks: int | None = None) -> bool:
        if not self.path.exists():
            return False
        with self.path.open("rb") as file:
            data = pickle.load(file)
        if data.get("version") != BM25_INDEX_VERSION:
            return False
        n_chunks = int(data.get("n_chunks", 0))
        if expected_chunks is not None and n_chunks != expected_chunks:
            return False
        self.tf_matrix = data["tf_matrix"]
        self.vocab = data["vocab"]
        self.idf_vec = data["idf_vec"]
        self.n_chunks = n_chunks
        self.version = data["version"]
        return True

    def search(self, query: str, top_k: int = 50) -> list[tuple[int, float]]:
        if self.tf_matrix is None or self.n_chunks == 0:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        q_vec = _build_query_vector(tokens, self.vocab, self.idf_vec)
        if q_vec is None:
            return []
        scores = (self.tf_matrix @ q_vec).ravel()
        nonzero = np.flatnonzero(scores)
        if len(nonzero) == 0:
            return []
        top_idx = nonzero[np.argsort(scores[nonzero])[::-1][:top_k]]
        return [(int(i), float(scores[i])) for i in top_idx if scores[i] > 0]


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
