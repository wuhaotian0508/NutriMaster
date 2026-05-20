#!/usr/bin/env python3
"""
一站式处理：ZIP 上传 → 提取 → 验证 → 自动索引更新

用法:
    python3 process_zip_and_index.py papers.zip
    python3 process_zip_and_index.py papers.zip --workers 4 --skip-index
"""

import argparse
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from datetime import datetime

# 设置环境变量（必须在导入 nutrimaster 之前）
REPO_ROOT = Path(__file__).parent
os.environ.setdefault("JSON_DIR", str(REPO_ROOT / "data" / "corpus"))
os.environ.setdefault("MD_DIR", str(REPO_ROOT / "src" / "nutrimaster" / "extraction" / "input"))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from nutrimaster.extraction.config import INPUT_DIR, PROCESSED_DIR, ensure_dirs
from nutrimaster.extraction.pipeline import run_pipeline_batch
from nutrimaster.extraction.token_tracker import TokenTracker
from nutrimaster.rag.jina import JinaRetriever


def extract_zip_to_input(zip_path: Path, input_dir: Path) -> dict:
    """
    解压 ZIP 文件中的所有 .md 文件到 input 目录

    返回:
        {
            "new_files": [...],      # 新解压的文件
            "skipped_existing": [...], # 已存在的文件
            "skipped_processed": [...] # 已处理过的文件
        }
    """
    input_dir = Path(input_dir)
    processed_dir = Path(PROCESSED_DIR)

    new_files = []
    skipped_existing = []
    skipped_processed = []

    print(f"📦 解压 ZIP: {zip_path.name}")

    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            if not info.filename.endswith('.md'):
                continue

            # 提取文件名（去除路径）
            filename = Path(info.filename).name

            # 检查是否已处理
            if (processed_dir / filename).exists():
                skipped_processed.append(filename)
                continue

            # 检查是否已存在于 input
            target_path = input_dir / filename
            if target_path.exists():
                skipped_existing.append(filename)
                continue

            # 解压到 input 目录
            with zf.open(info) as source:
                target_path.write_bytes(source.read())
            new_files.append(filename)

    return {
        "new_files": new_files,
        "skipped_existing": skipped_existing,
        "skipped_processed": skipped_processed,
    }


def process_papers(input_dir: Path, workers: int = 2) -> dict:
    """
    批量处理 input 目录中的所有 .md 文件

    返回:
        {
            "processed": int,
            "failed": int,
            "token_summary": {...}
        }
    """
    files = sorted([f for f in Path(input_dir).glob("*.md")])

    if not files:
        return {"processed": 0, "failed": 0, "token_summary": {}}

    print(f"\n🔄 开始处理 {len(files)} 个文件（{workers} 并行）...")

    tracker = TokenTracker(model=os.getenv("EXTRACTOR_MODEL", "unknown"))

    result = run_pipeline_batch(
        files,
        input_dir=input_dir,
        workers=workers,
        tracker=tracker,
    )

    return {
        "processed": result.get("done", 0),
        "failed": result.get("failed", 0),
        "token_summary": tracker.get_summary(),
    }


def rebuild_index(data_dir: Path, force: bool = False) -> dict:
    """
    增量重建 RAG 索引

    返回:
        {
            "total_chunks": int,
            "embedding_shape": tuple
        }
    """
    print(f"\n🔍 重建索引...")

    retriever = JinaRetriever()
    retriever.build_index(
        data_dir=data_dir,
        incremental=True,
        force=force
    )

    return {
        "total_chunks": len(retriever.chunks),
        "embedding_shape": retriever.embeddings.shape if retriever.embeddings is not None else None,
    }


def main():
    parser = argparse.ArgumentParser(
        description="一站式处理：ZIP → 提取 → 验证 → 索引更新"
    )
    parser.add_argument(
        "zip_file",
        type=Path,
        help="包含 .md 文件的 ZIP 文件路径"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="并行处理的 worker 数量（默认: 2）"
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="跳过索引重建步骤"
    )
    parser.add_argument(
        "--force-index",
        action="store_true",
        help="强制完全重建索引（而非增量）"
    )

    args = parser.parse_args()

    # 验证 ZIP 文件
    if not args.zip_file.exists():
        print(f"❌ 错误: ZIP 文件不存在: {args.zip_file}")
        sys.exit(1)

    # 确保目录存在
    ensure_dirs()
    input_dir = Path(INPUT_DIR)
    data_dir = Path(os.environ["JSON_DIR"])

    print("=" * 70)
    print(f"🚀 开始处理: {args.zip_file.name}")
    print(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 步骤 1: 解压 ZIP
    extract_result = extract_zip_to_input(args.zip_file, input_dir)

    print(f"\n📊 解压结果:")
    print(f"  新文件: {len(extract_result['new_files'])}")
    print(f"  已存在: {len(extract_result['skipped_existing'])}")
    print(f"  已处理: {len(extract_result['skipped_processed'])}")

    if extract_result['new_files']:
        print(f"\n  新文件列表（前 10 个）:")
        for fname in extract_result['new_files'][:10]:
            print(f"    - {fname}")
        if len(extract_result['new_files']) > 10:
            print(f"    ... 还有 {len(extract_result['new_files']) - 10} 个")

    # 步骤 2: 处理论文
    if extract_result['new_files'] or extract_result['skipped_existing']:
        process_result = process_papers(input_dir, workers=args.workers)

        print(f"\n📊 处理结果:")
        print(f"  成功: {process_result['processed']}")
        print(f"  失败: {process_result['failed']}")

        if process_result['token_summary']:
            summary = process_result['token_summary']
            print(f"\n💰 Token 使用:")
            print(f"  总 tokens: {summary.get('total_tokens', 0):,}")
            print(f"  预估成本: ${summary.get('total_cost', 0):.4f}")
    else:
        print(f"\n⏭️  没有新文件需要处理")
        process_result = {"processed": 0, "failed": 0}

    # 步骤 3: 重建索引
    if not args.skip_index and process_result['processed'] > 0:
        try:
            index_result = rebuild_index(data_dir, force=args.force_index)

            print(f"\n📊 索引更新:")
            print(f"  总 chunks: {index_result['total_chunks']:,}")
            if index_result['embedding_shape']:
                print(f"  Embedding shape: {index_result['embedding_shape']}")
        except Exception as e:
            print(f"\n❌ 索引重建失败: {e}")
            import traceback
            traceback.print_exc()
    elif args.skip_index:
        print(f"\n⏭️  跳过索引重建（--skip-index）")
    else:
        print(f"\n⏭️  没有新文件，跳过索引重建")

    print("\n" + "=" * 70)
    print("✅ 处理完成！")
    print("=" * 70)


if __name__ == "__main__":
    main()
