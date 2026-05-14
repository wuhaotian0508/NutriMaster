"""
高级统计分析 - 支持按 agent、版本、时间过滤

使用示例:
  # 从本地 JSONL 文件统计
  python -m eval.metrics.filter_stats --file results.jsonl --agent NutriMaster --version v3

  # 从 Notion 数据库统计
  python -m eval.metrics.filter_stats --notion --agent NutriMaster --after 2026-05-01

  # 统计特定时间段
  python -m eval.metrics.filter_stats --file results.jsonl --after 2026-05-01 --before 2026-05-07
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from eval.configs import NOTION_API_KEY
from eval.datamanager.local_storage import LocalStorage


def filter_results(
    results: list[dict[str, Any]],
    agent_name: str | None = None,
    version: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    question_id_min: int | None = None,
    question_id_max: int | None = None,
) -> list[dict[str, Any]]:
    """
    过滤评测结果

    Args:
        results: 原始结果列表
        agent_name: Agent 名称过滤
        version: 版本过滤
        after: 时间下界（包含）
        before: 时间上界（不包含）

    Returns:
        过滤后的结果列表
    """
    filtered = []

    for r in results:
        # Agent 名称过滤
        if agent_name and r.get("Agent名称") != agent_name:
            continue

        # 版本过滤
        if version and r.get("版本") != version:
            continue

        q_id = r.get("题目编号")
        if question_id_min is not None and (q_id is None or q_id < question_id_min):
            continue
        if question_id_max is not None and (q_id is None or q_id > question_id_max):
            continue

        # 时间过滤
        timestamp = r.get("时间戳") or r.get("timestamp")
        if timestamp:
            try:
                # 支持多种时间格式
                if isinstance(timestamp, str):
                    # ISO 8601 格式
                    if "T" in timestamp:
                        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    else:
                        # 简单日期格式
                        dt = datetime.strptime(timestamp, "%Y-%m-%d")
                else:
                    # Unix 时间戳
                    dt = datetime.fromtimestamp(timestamp)

                after_cmp = after
                before_cmp = before
                if dt.tzinfo:
                    if after_cmp and after_cmp.tzinfo is None:
                        after_cmp = after_cmp.replace(tzinfo=timezone.utc)
                    if before_cmp and before_cmp.tzinfo is None:
                        before_cmp = before_cmp.replace(tzinfo=timezone.utc)
                else:
                    if after_cmp and after_cmp.tzinfo:
                        after_cmp = after_cmp.replace(tzinfo=None)
                    if before_cmp and before_cmp.tzinfo:
                        before_cmp = before_cmp.replace(tzinfo=None)
                if after_cmp and dt < after_cmp:
                    continue
                if before_cmp and dt >= before_cmp:
                    continue
            except (ValueError, TypeError):
                # 时间解析失败，跳过时间过滤
                pass

        filtered.append(r)

    return filtered


def _parse_timestamp(value: Any) -> datetime:
    if not value:
        return datetime.min
    try:
        if isinstance(value, str):
            if "T" in value:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            return datetime.strptime(value, "%Y-%m-%d")
        return datetime.fromtimestamp(value)
    except (ValueError, TypeError):
        return datetime.min


def dedupe_latest_by_question(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 Agent/版本/题目只保留时间戳最新的一条，避免重复上传或 retry 影响均值。"""
    latest: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for row in results:
        key = (row.get("Agent名称"), row.get("版本"), row.get("题目编号"))
        if key not in latest:
            latest[key] = row
            continue
        row_ts = _parse_timestamp(row.get("时间戳") or row.get("timestamp"))
        old_ts = _parse_timestamp(latest[key].get("时间戳") or latest[key].get("timestamp"))
        if row_ts >= old_ts:
            latest[key] = row
    return list(latest.values())


def calc_filtered_stats(
    results: list[dict[str, Any]],
    agent_name: str | None = None,
    version: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    question_id_min: int | None = None,
    question_id_max: int | None = None,
    dedupe_latest: bool = False,
) -> dict[str, Any]:
    """
    计算过滤后的统计信息

    Returns:
        {
            "过滤条件": {...},
            "题目数": 10,
            "总得分": 85.0,
            "总满分": 100.0,
            "平均分": 8.5,
            "得分率": "85.00%",
            "详细结果": [...]
        }
    """
    filtered = filter_results(
        results,
        agent_name,
        version,
        after,
        before,
        question_id_min=question_id_min,
        question_id_max=question_id_max,
    )
    if dedupe_latest:
        filtered = dedupe_latest_by_question(filtered)

    if not filtered:
        return {
            "过滤条件": {
                "Agent名称": agent_name,
                "版本": version,
                "时间范围": f"{after or '开始'} ~ {before or '结束'}",
                "题目编号范围": f"{question_id_min or '开始'} ~ {question_id_max or '结束'}",
                "去重": dedupe_latest,
            },
            "题目数": 0,
            "总得分": 0.0,
            "总满分": 0.0,
            "平均分": 0.0,
            "得分率": "0.00%",
            "详细结果": [],
        }

    total_score = sum(r.get("总分") or 0 for r in filtered)
    total_max = sum(r.get("满分") or 0 for r in filtered)
    count = len(filtered)

    avg_score = total_score / count if count > 0 else 0
    score_rate = (total_score / total_max * 100) if total_max > 0 else 0

    return {
        "过滤条件": {
            "Agent名称": agent_name or "全部",
            "版本": version or "全部",
            "时间范围": f"{after or '开始'} ~ {before or '结束'}",
            "题目编号范围": f"{question_id_min or '开始'} ~ {question_id_max or '结束'}",
            "去重": dedupe_latest,
        },
        "题目数": count,
        "总得分": round(total_score, 2),
        "总满分": round(total_max, 2),
        "平均分": round(avg_score, 2),
        "得分率": f"{score_rate:.2f}%",
        "详细结果": filtered,
    }


def print_filtered_stats(stats: dict[str, Any], show_details: bool = False):
    """打印过滤后的统计信息"""
    print(f"\n{'='*60}")
    print("过滤条件:")
    for key, value in stats["过滤条件"].items():
        print(f"  {key}: {value}")
    print(f"{'='*60}")
    print(f"题目数: {stats['题目数']}")
    print(f"总得分: {stats['总得分']}")
    print(f"总满分: {stats['总满分']}")
    print(f"平均分: {stats['平均分']}")
    print(f"得分率: {stats['得分率']}")
    print(f"{'='*60}\n")

    if show_details and stats["详细结果"]:
        print("详细结果:")
        print(f"{'题目编号':<10} {'Agent':<20} {'版本':<10} {'得分':<10} {'满分':<10}")
        print("-" * 70)
        for r in sorted(stats["详细结果"], key=lambda x: x.get("题目编号") or 0):
            q_id = r.get("题目编号") or "N/A"
            agent = r.get("Agent名称") or "N/A"
            ver = r.get("版本") or "N/A"
            score = r.get("总分") or 0
            max_score = r.get("满分") or 0
            print(f"{q_id:<10} {agent:<20} {ver:<10} {score:<10.2f} {max_score:<10.2f}")
        print()


def load_results_from_file(filepath: str) -> list[dict[str, Any]]:
    """从 JSONL 文件加载结果"""
    return LocalStorage.load_results(filepath)


def load_results_from_notion(
    database_id: str,
    agent_name: str | None = None,
    version: str | None = None,
) -> list[dict[str, Any]]:
    """从 Notion 数据库加载结果"""
    from eval.datamanager.notion_storage import NotionStorage

    if not NOTION_API_KEY:
        raise ValueError("需要设置 NOTION_API_KEY 环境变量")

    storage = NotionStorage(NOTION_API_KEY)
    return storage.load_results(database_id, agent_name, version)


def main():
    parser = argparse.ArgumentParser(description="高级统计分析 - 支持按 agent、版本、时间过滤")

    # 数据源
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", type=str, help="本地 JSONL 文件路径")
    source_group.add_argument("--notion", action="store_true", help="从 Notion 数据库加载")

    parser.add_argument("--database-id", type=str, help="Notion 数据库 ID（使用 --notion 时需要）")

    # 过滤条件
    parser.add_argument("--agent", type=str, help="Agent 名称过滤")
    parser.add_argument("--version", type=str, help="版本过滤")
    parser.add_argument("--after", type=str, help="时间下界（格式: YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS）")
    parser.add_argument("--before", type=str, help="时间上界（格式: YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS）")
    parser.add_argument("--question-id-min", type=int, help="题目编号下界（包含）")
    parser.add_argument("--question-id-max", type=int, help="题目编号上界（包含）")
    parser.add_argument("--dedupe-latest", action="store_true", help="同一 Agent/版本/题目只保留最新记录")

    # 输出选项
    parser.add_argument("--details", action="store_true", help="显示详细结果")
    parser.add_argument("--output", type=str, help="输出 JSON 文件路径")

    args = parser.parse_args()

    # 加载数据
    if args.file:
        results = load_results_from_file(args.file)
    else:
        if not args.database_id:
            parser.error("使用 --notion 时需要提供 --database-id")
        results = load_results_from_notion(args.database_id, args.agent, args.version)

    # 解析时间
    after = None
    before = None
    if args.after:
        try:
            after = datetime.fromisoformat(args.after)
        except ValueError:
            after = datetime.strptime(args.after, "%Y-%m-%d")
    if args.before:
        try:
            before = datetime.fromisoformat(args.before)
        except ValueError:
            before = datetime.strptime(args.before, "%Y-%m-%d")

    # 计算统计
    stats = calc_filtered_stats(
        results,
        args.agent,
        args.version,
        after,
        before,
        question_id_min=args.question_id_min,
        question_id_max=args.question_id_max,
        dedupe_latest=args.dedupe_latest,
    )

    # 打印结果
    print_filtered_stats(stats, args.details)

    # 保存到文件
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        print(f"统计结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
