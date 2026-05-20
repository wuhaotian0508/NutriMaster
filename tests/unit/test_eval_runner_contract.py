import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.runner import QuestionEvaluator


class FakeAgent:
    name = "FakeAgent"

    def __init__(self, result):
        self.result = result

    async def answer(self, question: str):
        self.question = question
        return self.result


class FakeJudge:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def judge(self, question, answer, rubrics, reference_answer=""):
        self.calls.append((question, answer, rubrics, reference_answer))
        return self.result


def _question():
    return {
        "编号": 7,
        "标题": "测试题",
        "正文": "问题正文",
        "采分点": [{"描述": "采分点", "满分": 2.0}],
        "参考答案": "参考",
        "难度": "中等",
        "领域": "植物营养",
    }


def test_question_evaluator_success():
    agent = FakeAgent({"ok": True, "output": "答案", "error": None})
    judge = FakeJudge({
        "ok": True,
        "总分": 1.5,
        "满分": 2.0,
        "评分详情": "评分详情",
        "采分点得分": [1.5],
        "error": None,
    })
    evaluator = QuestionEvaluator(judge=judge, version="v1")

    result = asyncio.run(evaluator.evaluate(_question(), agent))

    assert result["题目编号"] == 7
    assert result["Agent名称"] == "FakeAgent"
    assert result["版本"] == "v1"
    assert result["答案"] == "答案"
    assert result["总分"] == 1.5
    assert result["满分"] == 2.0
    assert result["评分详情"] == "评分详情"
    assert result["采分点1-得分"] == 1.5
    assert result["题目标题"] == "测试题"
    assert result["难度等级"] == "中等"
    assert result["领域大类"] == "植物营养"
    assert judge.calls == [("问题正文", "答案", [{"描述": "采分点", "满分": 2.0}], "参考")]


def test_question_evaluator_agent_error_skips_judge():
    agent = FakeAgent({"ok": False, "output": "", "error": "agent failed"})
    judge = FakeJudge({"ok": True})
    evaluator = QuestionEvaluator(judge=judge, version="v1")

    result = asyncio.run(evaluator.evaluate(_question(), agent))

    assert result["总分"] == 0.0
    assert result["满分"] == 2.0
    assert result["答案"] == ""
    assert result["评分详情"] == "Agent 失败: agent failed"
    assert result["error"] == "agent failed"
    assert judge.calls == []


def test_question_evaluator_judge_error_keeps_agent_answer():
    agent = FakeAgent({"ok": True, "output": "答案", "error": None})
    judge = FakeJudge({"ok": False, "总分": 0.0, "满分": 2.0, "评分详情": "", "error": "judge failed"})
    evaluator = QuestionEvaluator(judge=judge, version="v1")

    result = asyncio.run(evaluator.evaluate(_question(), agent))

    assert result["总分"] == 0.0
    assert result["满分"] == 2.0
    assert result["答案"] == "答案"
    assert result["评分详情"] == "Judge 失败: judge failed"
    assert result["error"] == "judge failed"
