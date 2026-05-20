#!/usr/bin/env python3
"""
快速检查 RAG 索引状态

用法:
    python3 check_index_status.py
"""

import json
from pathlib import Path
from datetime import datetime

def format_time(timestamp):
    """格式化时间戳"""
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def format_number(n):
    """格式化数字（添加千位分隔符）"""
    return f"{n:,}"

def main():
    # 路径配置
    corpus_dir = Path("data/corpus")
    manifest_path = Path("data/index/manifest.json")
    chunks_path = Path("data/index/chunks.pkl")
    embeddings_path = Path("data/index/embeddings.npy")

    print("=" * 70)
    print("RAG 索引状态检查")
    print("=" * 70)
    print()

    # 1. 检查 corpus 文件
    print("📁 Corpus 目录:")
    if corpus_dir.exists():
        all_files = list(corpus_dir.glob("*_nutri_plant_verified.json"))
        print(f"   路径: {corpus_dir}")
        print(f"   文件数: {format_number(len(all_files))}")
    else:
        print(f"   ❌ 目录不存在: {corpus_dir}")
        return

    # 2. 检查 manifest
    print()
    print("📋 Manifest 文件:")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        indexed_files = len(manifest.get("files", {}))
        stat = manifest_path.stat()

        print(f"   路径: {manifest_path}")
        print(f"   已索引文件数: {format_number(indexed_files)}")
        print(f"   文件大小: {stat.st_size / 1024 / 1024:.2f} MB")
        print(f"   最后修改: {format_time(stat.st_mtime)}")
    else:
        print(f"   ❌ 文件不存在: {manifest_path}")
        indexed_files = 0

    # 3. 检查 chunks
    print()
    print("🧩 Chunks 文件:")
    if chunks_path.exists():
        stat = chunks_path.stat()
        print(f"   路径: {chunks_path}")
        print(f"   文件大小: {stat.st_size / 1024 / 1024:.2f} MB")
        print(f"   最后修改: {format_time(stat.st_mtime)}")
    else:
        print(f"   ❌ 文件不存在: {chunks_path}")

    # 4. 检查 embeddings
    print()
    print("🔢 Embeddings 文件:")
    if embeddings_path.exists():
        stat = embeddings_path.stat()
        print(f"   路径: {embeddings_path}")
        print(f"   文件大小: {stat.st_size / 1024 / 1024:.2f} MB")
        print(f"   最后修改: {format_time(stat.st_mtime)}")
    else:
        print(f"   ❌ 文件不存在: {embeddings_path}")

    # 5. 同步状态
    print()
    print("=" * 70)
    print("同步状态:")
    print("=" * 70)

    total_files = len(all_files)
    missing_files = total_files - indexed_files
    sync_percentage = (indexed_files / total_files * 100) if total_files > 0 else 0

    print(f"   总文件数: {format_number(total_files)}")
    print(f"   已索引: {format_number(indexed_files)}")
    print(f"   缺失: {format_number(missing_files)}")
    print(f"   同步率: {sync_percentage:.2f}%")
    print()

    if missing_files == 0:
        print("   ✅ 状态: 完全同步")
    elif missing_files < 10:
        print(f"   ⚠️  状态: 有 {missing_files} 个文件未索引（建议手动重建）")
    elif missing_files < 100:
        print(f"   ⚠️  状态: 有 {missing_files} 个文件未索引（建议尽快重建）")
    else:
        print(f"   ❌ 状态: 有 {missing_files} 个文件未索引（需要立即重建）")

    # 6. 缺失文件列表（如果不多的话）
    if 0 < missing_files <= 20:
        print()
        print("缺失的文件:")
        indexed_set = set(manifest.get("files", {}).keys())
        all_set = {f.name for f in all_files}
        missing_set = all_set - indexed_set
        for i, filename in enumerate(sorted(missing_set), 1):
            print(f"   {i}. {filename}")

    # 7. 重建建议
    if missing_files > 0:
        print()
        print("=" * 70)
        print("重建建议:")
        print("=" * 70)

        # 估算重建时间
        avg_time_per_file = 2.5  # 秒
        estimated_seconds = missing_files * avg_time_per_file
        estimated_minutes = estimated_seconds / 60

        print(f"   预计需要处理 {format_number(missing_files)} 个文件")
        print(f"   预计耗时: {estimated_minutes:.1f} 分钟 ({estimated_seconds:.0f} 秒)")
        print()
        print("   方法 1 - Admin Panel（推荐）:")
        print("      1. 访问 http://localhost:8000/admin")
        print("      2. 在 Dashboard 点击 '🔄 Rebuild Index' 按钮")
        print()
        print("   方法 2 - CLI 脚本:")
        print("      python3 rebuild_index_robust.py")
        print()
        print("   方法 3 - 后台运行:")
        print("      nohup python3 rebuild_index_robust.py > rebuild.log 2>&1 &")
        print("      tail -f rebuild.log  # 查看进度")

    print()
    print("=" * 70)

if __name__ == "__main__":
    main()
