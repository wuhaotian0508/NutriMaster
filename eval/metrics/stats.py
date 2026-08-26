"""结果分析小工具 - 区分有效评分与评测链路故障。"""

from collections import Counter
from typing import Any


FAILURE_STATUSES = {"agent_error", "judge_error", "runner_error"}


def evaluation_failure_type(result: dict[str, Any]) -> str | None:
    """返回评测失败类型，同时兼容没有 ``评测状态`` 的历史结果。"""
    status = str(result.get("评测状态") or "").strip()
    if status in FAILURE_STATUSES:
        return status

    error = str(result.get("error") or "").strip()
    details = str(result.get("评分详情") or "").strip()
    if details.startswith("Agent 失败:"):
        return "agent_error"
    if details.startswith("Judge 失败:"):
        return "judge_error"
    if error:
        return "runner_error"
    return None


def is_failed_result(result: dict[str, Any]) -> bool:
    """判断一条记录是否因评测基础设施失败而不可计分。"""
    return evaluation_failure_type(result) is not None


def calc_stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    计算统计信息

    results 格式: [{"题目编号": 1, "总分": 8.5, "满分": 10.0}, ...]

    失败记录仍保留在总题目数和失败分布中，但不会作为 0 分污染有效
    得分。合法的 0 分记录（例如答案为空且 Judge 正常评分）仍参与统计。
    """
    failed = [result for result in results if is_failed_result(result)]
    scored = [result for result in results if not is_failed_result(result)]
    failure_counts = Counter(
        failure_type
        for result in failed
        if (failure_type := evaluation_failure_type(result)) is not None
    )

    total_score = sum(result.get("总分") or 0 for result in scored)
    total_max = sum(result.get("满分") or 0 for result in scored)
    scored_count = len(scored)
    total_count = len(results)

    avg_score = total_score / scored_count if scored_count > 0 else 0
    score_rate = (total_score / total_max * 100) if total_max > 0 else 0
    success_rate = (scored_count / total_count * 100) if total_count > 0 else 0

    return {
        "题目数": total_count,
        "有效题目数": scored_count,
        "失败题目数": len(failed),
        "评测成功率": f"{success_rate:.2f}%",
        "失败分布": dict(sorted(failure_counts.items())),
        "总得分": round(total_score, 2),
        "总满分": round(total_max, 2),
        "平均分": round(avg_score, 2),
        "得分率": f"{score_rate:.2f}%",
    }


def print_stats(agent_name: str, version: str, stats: dict[str, Any]):
    """打印统计信息"""
    print(f"\n{'='*60}")
    print(f"Agent: {agent_name}")
    print(f"版本: {version}")
    print(f"{'='*60}")
    print(f"题目数: {stats['题目数']}")
    print(f"有效题目数: {stats['有效题目数']}")
    print(f"失败题目数: {stats['失败题目数']}")
    print(f"评测成功率: {stats['评测成功率']}")
    if stats["失败分布"]:
        print(f"失败分布: {stats['失败分布']}")
    print(f"总得分: {stats['总得分']}")
    print(f"总满分: {stats['总满分']}")
    print(f"平均分: {stats['平均分']}")
    print(f"得分率: {stats['得分率']}")
    print(f"{'='*60}\n")


def print_details(results: list[dict[str, Any]]):
    """打印详细结果"""
    print("详细结果:")
    print(f"{'题目编号':<10} {'得分':<10} {'满分':<10}")
    print("-" * 35)
    for r in sorted(results, key=lambda x: x.get("题目编号") or 0):
        q_id = r.get("题目编号") or "N/A"
        score = r.get("总分") or 0
        max_score = r.get("满分") or 0
        print(f"{q_id:<10} {score:<10.2f} {max_score:<10.2f}")
    print()
