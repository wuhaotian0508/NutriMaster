#!/usr/bin/env python3
"""Inspect former-data/data RAG index files: counts plus tail samples."""
from __future__ import annotations

import argparse
import json
import pickle
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def shorten(value: Any, limit: int = 500) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def to_plain(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "_asdict"):
        return obj._asdict()
    if hasattr(obj, "__dict__"):
        return vars(obj)
    return obj


def print_obj_tail(name: str, items: Any, tail: int, limit: int) -> None:
    n = len(items)
    print(f"\n== {name} ==")
    print(f"type: {type(items)!r}")
    print(f"count: {n}")
    start = max(0, n - tail)
    for i in range(start, n):
        item = to_plain(items[i])
        print(f"\n[{i}] type: {type(items[i])!r}")
        if isinstance(item, dict):
            print(f"keys: {list(item.keys())}")
        print(shorten(item, limit))


def inspect_corpus(root: Path, tail: int, limit: int) -> None:
    corpus_dir = root / "corpus"
    print("\n== corpus/*.json ==")
    if not corpus_dir.exists():
        print(f"missing: {corpus_dir}")
        return
    files = sorted(corpus_dir.glob("*.json"))
    print(f"count: {len(files)}")
    for path in files[-tail:]:
        print(f"\n{path.name}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                print(f"keys: {list(data.keys())}")
            print(shorten(data, limit))
        except Exception as exc:
            print(f"could not parse/read: {exc}")


def inspect_embeddings(index_dir: Path, tail: int) -> None:
    print("\n== embeddings.npy ==")
    candidates = [index_dir / "embeddings.npy"] + sorted(index_dir.glob(".embeddings.npy.*"))
    existing = [p for p in candidates if p.exists()]
    if not existing:
        print(f"missing: {index_dir / 'embeddings.npy'}")
        return

    path = existing[0]
    if path.name != "embeddings.npy":
        print(f"canonical embeddings.npy missing; found temporary file instead: {path.name}")
        print("this usually means rsync is still writing or a previous transfer was interrupted")

    try:
        import numpy as np

        arr = np.load(path, mmap_mode="r")
        print(f"file: {path}")
        print(f"shape: {arr.shape}")
        print(f"dtype: {arr.dtype}")
        rows = arr.shape[0] if arr.ndim else 0
        start = max(0, rows - tail)
        for i in range(start, rows):
            row = arr[i]
            preview = row[:10].tolist() if getattr(row, "ndim", 0) else row.item()
            print(f"row[{i}] first values: {preview}")
    except Exception as exc:
        print(f"could not load with numpy: {exc}")


def inspect_pickle(path: Path, name: str, tail: int, limit: int) -> Any | None:
    print(f"\n== {name} ==")
    if not path.exists():
        print(f"missing: {path}")
        return None
    try:
        with path.open("rb") as fh:
            obj = pickle.load(fh)
    except Exception as exc:
        print(f"could not unpickle: {exc}")
        return None

    print(f"file: {path}")
    print(f"type: {type(obj)!r}")
    try:
        print(f"count/len: {len(obj)}")
    except TypeError:
        pass

    if isinstance(obj, (list, tuple)):
        print_obj_tail(name + " tail", obj, tail, limit)
    elif isinstance(obj, dict):
        keys = list(obj.keys())
        print(f"keys count: {len(keys)}")
        print(f"tail keys: {keys[-tail:]}")
        for key in keys[-tail:]:
            print(f"\n[{key!r}] {shorten(obj[key], limit)}")
    else:
        attrs = [a for a in dir(obj) if not a.startswith("__")]
        print(f"attrs: {attrs[:80]}")
        for attr in ["corpus_size", "avgdl", "doc_len", "idf", "doc_freqs"]:
            if hasattr(obj, attr):
                value = getattr(obj, attr)
                print(f"\n{attr}: type={type(value)!r}")
                try:
                    print(f"{attr} len={len(value)}")
                except TypeError:
                    pass
                print(shorten(value, limit))
    return obj


def main() -> None:
    parser = argparse.ArgumentParser(description="Show counts and tail samples for RAG data files.")
    parser.add_argument("root", nargs="?", default=Path(__file__).resolve().parent, type=Path,
                        help="data root; defaults to the directory containing this script")
    parser.add_argument("--tail", type=int, default=3, help="number of tail records to show")
    parser.add_argument("--limit", type=int, default=800, help="max repr chars per object")
    args = parser.parse_args()

    root = args.root.resolve()
    index_dir = root / "index"
    print(f"root: {root}")
    print(f"index: {index_dir}")

    inspect_corpus(root, args.tail, args.limit)
    inspect_embeddings(index_dir, args.tail)
    inspect_pickle(index_dir / "chunks.pkl", "chunks.pkl", args.tail, args.limit)
    inspect_pickle(index_dir / "bm25.pkl", "bm25.pkl", args.tail, args.limit)


if __name__ == "__main__":
    main()
