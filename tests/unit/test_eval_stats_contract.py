from __future__ import annotations

from eval.metrics.filter_stats import calc_filtered_stats
from eval.metrics.stats import calc_stats, is_failed_result
from eval.run_manager import RunManager


def test_stats_exclude_infrastructure_failures_but_keep_valid_zero_scores():
    results = [
        {"题目编号": 1, "总分": 8.0, "满分": 10.0, "评测状态": "scored"},
        {"题目编号": 2, "总分": 0.0, "满分": 10.0, "评测状态": "scored"},
        {
            "题目编号": 3,
            "总分": 0.0,
            "满分": 10.0,
            "评测状态": "judge_error",
            "error": "timeout",
        },
    ]

    stats = calc_stats(results)

    assert stats == {
        "题目数": 3,
        "有效题目数": 2,
        "失败题目数": 1,
        "评测成功率": "66.67%",
        "失败分布": {"judge_error": 1},
        "总得分": 8.0,
        "总满分": 20.0,
        "平均分": 4.0,
        "得分率": "40.00%",
    }


def test_stats_recognize_legacy_judge_failures_without_error_field():
    legacy = {"总分": 0.0, "满分": 10.0, "评分详情": "Judge 失败: "}

    assert is_failed_result(legacy)
    assert calc_stats([legacy])["有效题目数"] == 0
    assert calc_filtered_stats([legacy])["失败分布"] == {"judge_error": 1}


def test_retry_failed_does_not_retry_a_legitimate_zero_score(tmp_path):
    manager = RunManager(checkpoint_dir=str(tmp_path), enable_progress=False)
    questions = [{"编号": 1}, {"编号": 2}]
    completed = {
        1: {"题目编号": 1, "总分": 0.0, "满分": 10.0, "评测状态": "scored"},
        2: {"题目编号": 2, "总分": 0.0, "满分": 10.0, "评测状态": "agent_error"},
    }

    remaining = manager.filter_remaining_questions(questions, completed, retry_failed=True)

    assert remaining == [{"编号": 2}]
