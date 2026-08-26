"""Evaluation orchestration for agents, questions, and run checkpoints."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from eval.agent_factory import iter_agents
from eval.configs import JUDGE_MODEL
from eval.contracts import EvalAgent
from eval.datamanager import save_local_results
from eval.judge.llm_judge import LLMJudge
from eval.metrics.stats import calc_stats, print_stats
from eval.run_manager import RunManager


_GENERATION_ID_RE = re.compile(r"^[0-9a-f]{64}$")


@lru_cache(maxsize=1)
def _repository_metadata() -> dict[str, str]:
    """Capture code and index identities once for reproducible result rows."""
    root = Path(__file__).resolve().parents[1]
    metadata: dict[str, str] = {}
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        revision = completed.stdout.strip()
        if revision:
            metadata["代码提交"] = revision
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        metadata["工作区状态"] = "dirty" if status.stdout.strip() else "clean"
    except (OSError, subprocess.SubprocessError):
        pass

    current_path = root / "data" / "index" / "CURRENT"
    try:
        generation_id = current_path.read_text(encoding="utf-8").strip()
    except OSError:
        generation_id = ""
    if _GENERATION_ID_RE.fullmatch(generation_id):
        metadata["索引代次"] = generation_id
    return metadata


def _new_run_metadata() -> dict[str, str]:
    run_id = os.getenv("EVAL_RUN_ID") or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:8]}"
    )
    return {"运行ID": run_id, **_repository_metadata()}


def _agent_model(agent: EvalAgent) -> str:
    for attribute in ("model_id", "model"):
        value = getattr(agent, attribute, None)
        if value:
            return str(value)
    return ""


def _question_result_metadata(question: dict[str, Any]) -> dict[str, Any]:
    """Carry question metadata into local/Notion-compatible result rows."""
    metadata: dict[str, Any] = {
        "运行轮次": "Run1",
        "评分模型": JUDGE_MODEL.split("/")[-1],
    }
    for source_key, result_key in (
        ("标题", "题目标题"),
        ("难度", "难度等级"),
        ("领域", "领域大类"),
    ):
        if question.get(source_key):
            metadata[result_key] = question[source_key]
    return metadata


def _rubric_score_props(judge_result: dict[str, Any]) -> dict[str, float]:
    scores = judge_result.get("采分点得分") or []
    return {f"采分点{i}-得分": score for i, score in enumerate(scores, 1)}


async def _close_agent_if_needed(agent: EvalAgent) -> None:
    close = getattr(agent, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


class QuestionEvaluator:
    """Evaluate one question with one agent and the configured judge."""

    def __init__(
        self,
        judge: LLMJudge,
        version: str,
        judge_sem: asyncio.Semaphore | None = None,
    ):
        self.judge = judge
        self.version = version
        self.judge_sem = judge_sem
        self.run_metadata = _new_run_metadata()

    async def evaluate(self, question: dict[str, Any], agent: EvalAgent) -> dict[str, Any]:
        q_id = question.get("编号", 0)
        q_text = question.get("正文", "")
        rubrics = question.get("采分点", [])
        result_metadata = {**_question_result_metadata(question), **self.run_metadata}
        agent_model = _agent_model(agent)
        if agent_model:
            result_metadata["被测模型"] = agent_model

        print(f"  … 题目 {q_id} - Agent 开始回答", flush=True)
        start = time.time()
        agent_result = await agent.answer(q_text)
        duration = time.time() - start

        agent_error = agent_result.get("error")
        if agent_error:
            print(f"  ✗ 题目 {q_id} - Agent 失败: {agent_error}", flush=True)
            return {
                **result_metadata,
                "题目编号": q_id,
                "Agent名称": agent.name,
                "版本": self.version,
                "答案": "",
                "总分": 0.0,
                "满分": sum(r.get("满分", 0) for r in rubrics),
                "评分详情": f"Agent 失败: {agent_error}",
                "耗时": duration,
                "评测状态": "agent_error",
                "error": agent_error,
            }

        answer = agent_result.get("output", "")
        print(f"  ✓ 题目 {q_id} - Agent 回答完成 ({duration:.2f}s)", flush=True)

        print(f"  … 题目 {q_id} - Judge 开始评分", flush=True)
        if self.judge_sem is None:
            judge_result = await self.judge.judge(q_text, answer, rubrics, question.get("参考答案", ""))
        else:
            async with self.judge_sem:
                judge_result = await self.judge.judge(q_text, answer, rubrics, question.get("参考答案", ""))

        if judge_result.get("ok"):
            print(f"  ✓ 题目 {q_id} - Judge 评分: {judge_result['总分']:.2f}/{judge_result['满分']:.2f}", flush=True)
            return {
                **result_metadata,
                **_rubric_score_props(judge_result),
                "题目编号": q_id,
                "Agent名称": agent.name,
                "版本": self.version,
                "答案": answer,
                "总分": judge_result["总分"],
                "满分": judge_result["满分"],
                "评分详情": judge_result["评分详情"],
                "耗时": duration,
                "评测状态": "scored",
            }

        judge_error = judge_result.get("error") or "未知错误"
        print(f"  ✗ 题目 {q_id} - Judge 失败: {judge_error}", flush=True)
        return {
            **result_metadata,
            "题目编号": q_id,
            "Agent名称": agent.name,
            "版本": self.version,
            "答案": answer,
            "总分": 0.0,
            "满分": sum(r.get("满分", 0) for r in rubrics),
            "评分详情": f"Judge 失败: {judge_error}",
            "耗时": duration,
            "评测状态": "judge_error",
            "error": judge_error,
        }


async def run_single_eval(
    question: dict[str, Any],
    agent: EvalAgent,
    judge: LLMJudge,
    version: str,
    judge_sem: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for evaluating one question."""
    return await QuestionEvaluator(judge=judge, version=version, judge_sem=judge_sem).evaluate(question, agent)


class EvaluationRunner:
    """Run full evaluations for one or more concrete eval agents."""

    def __init__(
        self,
        run_manager: RunManager,
        judge: LLMJudge,
        version: str,
        judge_concurrency: int = 3,
        resume: bool = False,
        retry_failed: bool = False,
        clean: bool = False,
    ):
        self.run_manager = run_manager
        self.version = version
        self.resume = resume
        self.retry_failed = retry_failed
        self.clean = clean
        self.question_evaluator = QuestionEvaluator(
            judge=judge,
            version=version,
            judge_sem=asyncio.Semaphore(judge_concurrency),
        )

    async def run(
        self,
        questions: list[dict[str, Any]],
        agent_types: list[str],
        llm_model: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        all_results: dict[str, list[dict[str, Any]]] = {}
        for agent in iter_agents(agent_types, llm_model=llm_model):
            results = await self.run_agent(agent, questions)
            all_results[f"{agent.name}/{self.version}"] = results
        return all_results

    async def run_agent(
        self,
        agent: EvalAgent,
        questions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        try:
            if self.clean:
                self.run_manager.clear_checkpoint(agent.name, self.version)
                print(f"🗑️  已清除检查点: {agent.name}/{self.version}")

            async def eval_single_question(question: dict[str, Any]) -> dict[str, Any]:
                return await self.question_evaluator.evaluate(question, agent)

            results = await self.run_manager.run_with_resume(
                agent_name=agent.name,
                version=self.version,
                questions=questions,
                eval_fn=eval_single_question,
                resume=self.resume,
                retry_failed=self.retry_failed,
            )

            stats = calc_stats(results)
            print_stats(agent.name, self.version, stats)
            save_local_results(results, agent.name, self.version)
            return results

        finally:
            await _close_agent_if_needed(agent)
