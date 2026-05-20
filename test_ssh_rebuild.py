#!/usr/bin/env python3
"""
测试 SSH 环境下的索引重建（带心跳输出）
"""
import sys
import time
from pathlib import Path

# 强制无缓冲输出
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

print("=" * 70, flush=True)
print("开始测试索引重建（SSH 环境）", flush=True)
print("=" * 70, flush=True)

# 导入并测试
print("\n导入 JinaRetriever...", flush=True)
from nutrimaster.rag.jina import JinaRetriever

print("初始化 retriever...", flush=True)
retriever = JinaRetriever()

print(f"当前索引状态: {retriever.index_status()}", flush=True)

# 测试小批量嵌入
print("\n测试嵌入功能...", flush=True)
test_texts = ["test text 1", "test text 2", "test text 3"]
embeddings = retriever._embed_texts(test_texts)
print(f"✅ 嵌入测试成功，shape: {embeddings.shape}", flush=True)

print("\n=" * 70, flush=True)
print("测试完成！现在可以运行完整的索引重建。", flush=True)
print("=" * 70, flush=True)
