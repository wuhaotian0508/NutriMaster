"""
Metrics - 评测结果统计分析
"""

from eval.metrics.stats import calc_stats, print_stats, print_details

__all__ = [
    "calc_stats",
    "print_stats",
    "print_details",
    "filter_results",
    "calc_filtered_stats",
    "print_filtered_stats",
]


def __getattr__(name):
    """延迟导入 filter_stats，避免 `python -m eval.metrics.filter_stats` 的 runpy 警告。"""
    if name in {"filter_results", "calc_filtered_stats", "print_filtered_stats"}:
        from eval.metrics import filter_stats

        return getattr(filter_stats, name)
    raise AttributeError(name)
