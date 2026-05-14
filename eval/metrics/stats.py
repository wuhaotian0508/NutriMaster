"""
结果分析小工具 - 计算平均分、得分率等
"""

from typing import Any


def calc_stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    计算统计信息

    results 格式: [{"题目编号": 1, "总分": 8.5, "满分": 10.0}, ...]

    返回: {"题目数": 10, "总得分": 85.0, "总满分": 100.0, "平均分": 8.5, "得分率": "85.00%"}
    """
    if not results:
        return {"题目数": 0, "总得分": 0.0, "总满分": 0.0, "平均分": 0.0, "得分率": "0.00%"}

    total_score = sum(r.get("总分") or 0 for r in results)
    total_max = sum(r.get("满分") or 0 for r in results)
    count = len(results)

    avg_score = total_score / count if count > 0 else 0
    score_rate = (total_score / total_max * 100) if total_max > 0 else 0

    return {
        "题目数": count,
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
