"""
计算指定 agent 指定版本的平均分

使用示例:
  python eval/metrics/calc_score.py NutriMaster v3
  python eval/metrics/calc_score.py "Claude-4.5-Sonnet" v3 --show-details
  python eval/metrics/calc_score.py NutriMaster v3 --date 2026-05-06
"""

import argparse
import os
import sys

import dotenv

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv()

from eval.datamanager.notion_storage import NotionStorage
from eval.metrics.stats import calc_stats, print_stats, print_details


def main():
    parser = argparse.ArgumentParser(description="计算指定 agent 指定版本的平均分")
    parser.add_argument("agent", help="Agent 名称")
    parser.add_argument("version", help="版本号")
    parser.add_argument("--date", help="只统计指定日期的记录，格式: 2026-05-06")
    parser.add_argument("--show-details", action="store_true", help="显示详细得分")
    parser.add_argument(
        "--result-db",
        default=os.getenv("RESULT_DB_ID", "c7b1b42c0ac14b5f883725f75860860e"),
        help="结果数据库 ID",
    )

    args = parser.parse_args()

    # 加载结果
    date_info = f", 日期={args.date}" if args.date else ""
    print(f"正在查询 Agent={args.agent}, 版本={args.version}{date_info}...")

    storage = NotionStorage()
    results = storage.load_results(
        database_id=args.result_db,
        agent_name=args.agent,
        version=args.version,
    )

    # 日期过滤（如果需要）
    if args.date:
        # TODO: 需要在 NotionStorage.load_results 中添加日期过滤支持
        print("警告: 日期过滤功能尚未实现")

    if not results:
        print(f"\n未找到任何结果")
        sys.exit(1)

    # 计算统计
    stats = calc_stats(results)
    print_stats(args.agent, args.version, stats)

    # 显示详细结果
    if args.show_details:
        print_details(results)


if __name__ == "__main__":
    main()
