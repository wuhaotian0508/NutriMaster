#!/usr/bin/env python3
"""
验证部署是否成功

直接测试索引功能，无需认证
"""

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from nutrimaster.rag.gene_index import JinaRetriever

def main():
    print("=" * 70)
    print("部署验证")
    print("=" * 70)
    print()

    # 1. 检查索引文件
    print("📁 检查索引文件:")
    data_dir = Path("data")
    manifest_path = data_dir / "index" / "manifest.json"
    chunks_path = data_dir / "index" / "chunks.pkl"
    embeddings_path = data_dir / "index" / "embeddings.npy"

    if not manifest_path.exists():
        print("   ❌ manifest.json 不存在")
        return False
    if not chunks_path.exists():
        print("   ❌ chunks.pkl 不存在")
        return False
    if not embeddings_path.exists():
        print("   ❌ embeddings.npy 不存在")
        return False

    print("   ✅ 所有索引文件存在")
    print()

    # 2. 加载索引
    print("🔄 加载索引:")
    try:
        retriever = JinaRetriever(data_dir=data_dir)
        print(f"   ✅ 索引加载成功")
        print(f"   📊 Chunks 数量: {len(retriever.chunks):,}")
        print(f"   📊 Embeddings shape: {retriever.embeddings.shape}")
    except Exception as e:
        print(f"   ❌ 索引加载失败: {e}")
        return False

    print()

    # 3. 测试检索功能
    print("🔍 测试检索功能:")
    try:
        query = "vitamin biosynthesis genes"
        results = retriever.search(query, top_k=3)
        print(f"   ✅ 检索成功，返回 {len(results)} 个结果")
        if results:
            print(f"   📄 第一个结果来自: {results[0].metadata.get('paper_id', 'N/A')}")
    except Exception as e:
        print(f"   ❌ 检索失败: {e}")
        return False

    print()

    # 4. 检查 corpus 同步状态
    print("📊 检查同步状态:")
    corpus_dir = data_dir / "corpus"
    corpus_files = list(corpus_dir.glob("*_nutri_plant_verified.json"))

    import json
    manifest = json.loads(manifest_path.read_text())
    indexed_files = len(manifest.get("files", {}))
    total_files = len(corpus_files)
    missing = total_files - indexed_files

    print(f"   总文件数: {total_files:,}")
    print(f"   已索引: {indexed_files:,}")
    print(f"   缺失: {missing:,}")

    if missing == 0:
        print("   ✅ 完全同步")
    else:
        print(f"   ⚠️  有 {missing} 个文件未索引")

    print()
    print("=" * 70)

    if missing == 0:
        print("✅ 部署验证成功！所有功能正常工作。")
        print()
        print("下一步:")
        print("  1. 访问 http://localhost:5000/admin 登录 Admin Panel")
        print("  2. 查看 Dashboard 的索引状态卡片")
        print("  3. 测试手动重建索引功能")
        print("  4. 上传 ZIP → 运行 Pipeline，验证自动索引更新")
        return True
    else:
        print("⚠️  索引未完全同步，建议运行:")
        print("  python3 rebuild_index_robust.py")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
