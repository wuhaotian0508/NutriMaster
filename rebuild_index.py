#!/usr/bin/env python3
"""
手动重建 RAG 索引（增量模式）
用于将新提取的 JSON 文件添加到 manifest.json 和索引中
"""

from pathlib import Path
from nutrimaster.rag.jina import JinaRetriever

def main():
    data_dir = Path("data/corpus")

    print(f"开始增量索引重建...")
    print(f"数据目录: {data_dir}")

    retriever = JinaRetriever()
    retriever.build_index(
        data_dir=data_dir,
        incremental=True,  # 增量模式：只处理新文件和修改过的文件
        force=False        # 不强制重建：保留已有索引
    )

    print(f"\n✅ 索引重建完成！")
    print(f"总 chunks: {retriever.total_chunks}")

if __name__ == "__main__":
    main()
