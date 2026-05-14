"""
计算指定 agent 指定版本的平均分

使用示例:
  python eval/metrics/calc_score.py NutriMaster v3
  python eval/metrics/calc_score.py "Claude-4.5-Sonnet" v3 --show-details
  python eval/metrics/calc_score.py NutriMaster v3 --date 2026-05-06
"""

import argparse
import sys
from datetime import datetime, timedelta

from eval.configs import RESULT_DB_ID
from eval.datamanager.notion_storage import NotionStorage
from eval.metrics.filter_stats import dedupe_latest_by_question, filter_results
from eval.metrics.stats import calc_stats, print_stats, print_details


def main():
    parser = argparse.ArgumentParser(description="计算指定 agent 指定版本的平均分")
    parser.add_argument("agent", help="Agent 名称")
    parser.add_argument("version", help="版本号")
    parser.add_argument("--date", help="只统计指定日期的记录，格式: 2026-05-06")
    parser.add_argument("--question-id-min", type=int, help="题目编号下界（包含）")
    parser.add_argument("--question-id-max", type=int, help="题目编号上界（包含）")
    parser.add_argument("--dedupe-latest", action="store_true", help="同一题只保留最新一条记录")
    parser.add_argument("--show-details", action="store_true", help="显示详细得分")
    parser.add_argument(
        "--result-db",
        default=RESULT_DB_ID,
        help="结果数据库 ID",
    )

    args = parser.parse_args()

    # 加载结果
    filters = []
    if args.date:
        filters.append(f"日期={args.date}")
    if args.question_id_min is not None or args.question_id_max is not None:
        filters.append(f"题号={args.question_id_min or '开始'}~{args.question_id_max or '结束'}")
    if args.dedupe_latest:
        filters.append("同题保留最新")
    filter_info = f", {', '.join(filters)}" if filters else ""
    print(f"正在查询 Agent={args.agent}, 版本={args.version}{filter_info}...")

    storage = NotionStorage()
    results = storage.load_results(
        database_id=args.result_db,
        agent_name=args.agent,
        version=args.version,
    )

    if args.date:
        after = datetime.strptime(args.date, "%Y-%m-%d")
        before = after + timedelta(days=1)
        results = filter_results(results, after=after, before=before)
    results = filter_results(
        results,
        question_id_min=args.question_id_min,
        question_id_max=args.question_id_max,
    )
    if args.dedupe_latest:
        results = dedupe_latest_by_question(results)

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
