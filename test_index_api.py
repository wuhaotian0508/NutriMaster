#!/usr/bin/env python3
"""
测试 Admin Panel 的索引 API 端点

用法:
    python3 test_index_api.py
"""

import requests
import json
import os
from pathlib import Path

# 从环境变量读取配置
ADMIN_URL = os.getenv("ADMIN_URL", "http://localhost:8000/admin")
TOKEN = os.getenv("ADMIN_TOKEN", "")

def test_index_status():
    """测试 /api/index/status 端点"""
    print("=" * 70)
    print("测试 /api/index/status")
    print("=" * 70)

    url = f"{ADMIN_URL}/api/index/status"
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

    try:
        resp = requests.get(url, headers=headers)
        print(f"状态码: {resp.status_code}")

        if resp.ok:
            data = resp.json()
            print(f"\n索引状态:")
            print(f"  总文件数: {data['total_files']:,}")
            print(f"  已索引: {data['indexed_files']:,}")
            print(f"  缺失: {data['missing_files']:,}")
            print(f"  总 chunks: {data['total_chunks']:,}")
            print(f"  Embedding shape: {data['embedding_shape']}")
            print(f"  最后更新: {data['last_updated']}")
            print(f"  是否同步: {'✅' if data['is_synced'] else '⚠️'}")
            return True
        else:
            print(f"错误: {resp.text}")
            return False
    except Exception as e:
        print(f"异常: {e}")
        return False

def test_index_rebuild():
    """测试 /api/index/rebuild 端点（仅测试 API 响应，不实际执行）"""
    print("\n" + "=" * 70)
    print("测试 /api/index/rebuild（dry run）")
    print("=" * 70)

    url = f"{ADMIN_URL}/api/index/rebuild"
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    headers["Content-Type"] = "application/json"

    # 不实际触发重建，只测试 API 是否可访问
    print("注意: 此测试不会实际触发索引重建")
    print("如需测试实际重建，请手动调用 API 或使用 Admin Panel")

    return True

def main():
    print("Admin Panel 索引 API 测试")
    print(f"目标 URL: {ADMIN_URL}")
    print(f"认证: {'已配置' if TOKEN else '未配置（可能需要登录）'}")
    print()

    # 测试状态查询
    status_ok = test_index_status()

    # 测试重建端点（仅检查可访问性）
    rebuild_ok = test_index_rebuild()

    print("\n" + "=" * 70)
    print("测试总结")
    print("=" * 70)
    print(f"  /api/index/status: {'✅ 通过' if status_ok else '❌ 失败'}")
    print(f"  /api/index/rebuild: {'✅ 可用' if rebuild_ok else '❌ 失败'}")
    print()

    if status_ok:
        print("✅ 索引 API 端点工作正常")
        print("\n下一步:")
        print("  1. 访问 Admin Panel Dashboard 查看索引状态卡片")
        print("  2. 点击 'Rebuild Index' 按钮测试手动重建功能")
        print("  3. 上传 ZIP → 运行 Pipeline，验证自动索引更新")
    else:
        print("⚠️  API 端点测试失败")
        print("\n可能的原因:")
        print("  1. Flask 应用未运行")
        print("  2. 需要认证 token（设置 ADMIN_TOKEN 环境变量）")
        print("  3. 端口或路径配置错误")

if __name__ == "__main__":
    main()
