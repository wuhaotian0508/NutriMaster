#!/usr/bin/env python3 -u
"""
手动重建 RAG 索引（增量模式，带详细进度和无缓冲输出）

使用 -u 标志确保 stdout/stderr 无缓冲，实时输出日志
"""

import json
import logging
import sys
from pathlib import Path
from datetime import datetime

# 强制无缓冲输出
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# 设置日志 - 直接输出到 stdout，无缓冲
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
    force=True
)

logger = logging.getLogger(__name__)

def main():
    data_dir = Path("data/corpus")
    manifest_path = Path("data/index/manifest.json")

    print("=" * 70, flush=True)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print("=" * 70, flush=True)

    # 检查当前状态
    print("\n检查当前索引状态...", flush=True)
    manifest = json.loads(manifest_path.read_text())
    indexed_files = set(manifest['files'].keys())
    all_files = {f.name for f in data_dir.glob('*_nutri_plant_verified.json')}
    missing = all_files - indexed_files

    print(f"总文件数: {len(all_files)}", flush=True)
    print(f"已索引: {len(indexed_files)}", flush=True)
    print(f"缺失: {len(missing)}", flush=True)
    print("=" * 70, flush=True)

    if not missing:
        print("✅ 所有文件都已索引！", flush=True)
        return

    print(f"\n开始增量索引重建...", flush=True)
    print(f"预计需要处理 {len(missing)} 个文件", flush=True)
    print("这可能需要几分钟到几十分钟，取决于 API 速度...", flush=True)
    print("-" * 70, flush=True)

    # 导入 JinaRetriever（延迟导入以便先输出状态信息）
    from nutrimaster.rag.jina import JinaRetriever

    print("正在初始化 JinaRetriever...", flush=True)
    retriever = JinaRetriever()

    print("开始构建索引（增量模式）...", flush=True)
    retriever.build_index(
        data_dir=data_dir,
        incremental=True,
        force=False
    )

    print("-" * 70, flush=True)
    print(f"✅ 索引重建完成！", flush=True)
    print(f"总 chunks: {len(retriever.chunks)}", flush=True)

    # 验证结果
    manifest = json.loads(manifest_path.read_text())
    print(f"更新后的文件数: {len(manifest['files'])}", flush=True)

    print("=" * 70, flush=True)
    print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print("=" * 70, flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 错误: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
