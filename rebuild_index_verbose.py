#!/usr/bin/env python3
"""
手动重建 RAG 索引（增量模式，带详细进度）
"""

import json
import logging
from pathlib import Path
from nutrimaster.rag.jina import JinaRetriever

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def main():
    data_dir = Path("data/corpus")
    manifest_path = Path("data/index/manifest.json")

    # 检查当前状态
    print("=" * 60)
    print("检查当前索引状态...")
    manifest = json.loads(manifest_path.read_text())
    indexed_files = set(manifest['files'].keys())
    all_files = {f.name for f in data_dir.glob('*_nutri_plant_verified.json')}
    missing = all_files - indexed_files

    print(f"总文件数: {len(all_files)}")
    print(f"已索引: {len(indexed_files)}")
    print(f"缺失: {len(missing)}")
    print("=" * 60)

    if not missing:
        print("✅ 所有文件都已索引！")
        return

    print(f"\n开始增量索引重建...")
    print(f"预计需要处理 {len(missing)} 个文件")
    print("这可能需要几分钟到几十分钟，取决于 API 速度...")
    print("-" * 60)

    retriever = JinaRetriever()
    retriever.build_index(
        data_dir=data_dir,
        incremental=True,
        force=False
    )

    print("-" * 60)
    print(f"✅ 索引重建完成！")
    print(f"总 chunks: {len(retriever.chunks)}")

    # 验证结果
    manifest = json.loads(manifest_path.read_text())
    print(f"更新后的文件数: {len(manifest['files'])}")

if __name__ == "__main__":
    main()
