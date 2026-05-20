#!/usr/bin/env python3
"""
快速统计脚本 - 从 Notion 查询特定 agent 的统计信息

使用示例:
  python quick_stats.py Evomaster-fs_mv v3 --after 2026-05-05
  python quick_stats.py NutriMaster v3
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval.metrics.filter_stats import calc_filtered_stats, print_filtered_stats, load_results_from_notion


def main():
    parser = argparse.ArgumentParser(description="快速统计 - 从 Notion 查询")
    parser.add_argument("agent", help="Agent 名称")
    parser.add_argument("version", help="版本")
    parser.add_argument("--after", help="时间下界（格式: YYYY-MM-DD）")
    parser.add_argument("--before", help="时间上界（格式: YYYY-MM-DD）")
    parser.add_argument("--details", action="store_true", help="显示详细结果")

    args = parser.parse_args()

    # 从环境变量获取配置
    database_id = os.getenv("RESULT_DB_ID", "c7b1b42c0ac14b5f883725f75860860e")

    print(f"正在从 Notion 加载数据...")
    print(f"  Agent: {args.agent}")
    print(f"  版本: {args.version}")
    if args.after:
        print(f"  时间范围: {args.after} 之后")

    # 加载数据
    try:
        results = load_results_from_notion(database_id, args.agent, args.version)
        print(f"✓ 加载了 {len(results)} 条结果\n")
    except Exception as e:
        print(f"✗ 加载失败: {e}")
        print("\n提示: 请确保设置了 NOTION_API_KEY 环境变量")
        return

    # 解析时间
    after = None
    before = None
    if args.after:
        after = datetime.strptime(args.after, "%Y-%m-%d")
    if args.before:
        before = datetime.strptime(args.before, "%Y-%m-%d")

    # 计算统计
    stats = calc_filtered_stats(results, args.agent, args.version, after, before)

    # 打印结果
    print_filtered_stats(stats, args.details)


if __name__ == "__main__":
    main()
