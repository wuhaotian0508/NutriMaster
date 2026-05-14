from __future__ import annotations

import pickle
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

BM25_INDEX_VERSION = "v1"

try:
    import jieba

    jieba.initialize()
    _HAS_JIEBA = True
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
    """BM25 sparse retriever aligned with the canonical GeneChunk order."""

    def __init__(self, index_path: Path):
        self.index_path = Path(index_path)
        self.bm25: BM25Okapi | None = None
        self.tokenized: list[list[str]] = []
        self.n_chunks = 0
        self.version = BM25_INDEX_VERSION

    @property
    def path(self) -> Path:
        return self.index_path / "bm25.pkl"

    def build(self, chunks: Sequence[Any]) -> None:
        self.tokenized = [tokenize(chunk_to_bm25_text(chunk)) for chunk in chunks]
        self.n_chunks = len(chunks)
        self.bm25 = BM25Okapi(self.tokenized) if self.tokenized else None
        self.version = BM25_INDEX_VERSION

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".pkl.tmp")
        with tmp_path.open("wb") as file:
            pickle.dump(
                {
                    "version": self.version,
                    "bm25": self.bm25,
                    "tokenized": self.tokenized,
                    "n_chunks": self.n_chunks,
                },
                file,
            )
        tmp_path.replace(self.path)

    def load(self, *, expected_chunks: int | None = None) -> bool:
        if not self.path.exists():
            return False
        with self.path.open("rb") as file:
            data = pickle.load(file)
        n_chunks = int(data.get("n_chunks", 0))
        if expected_chunks is not None and n_chunks != expected_chunks:
            return False
        if data.get("version") != BM25_INDEX_VERSION:
            return False
        self.bm25 = data.get("bm25")
        self.tokenized = data.get("tokenized", [])
        self.n_chunks = n_chunks
        self.version = data.get("version", BM25_INDEX_VERSION)
        return True

    def search(self, query: str, top_k: int = 50) -> list[tuple[int, float]]:
        if self.bm25 is None or self.n_chunks == 0:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores = np.asarray(self.bm25.get_scores(query_tokens), dtype=float)
        if not np.any(scores > 0):
            query_set = set(query_tokens)
            scores = np.asarray(
                [sum(1 for token in tokens if token in query_set) for tokens in self.tokenized],
                dtype=float,
            )
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
