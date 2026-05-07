"""
NutriBench 评测主脚本

使用示例:
  # 使用环境变量配置的 Agent
  EVAL_AGENTS=llm,nutrimaster python eval/main.py

  # 指定单个 Agent
  python eval/main.py --agents llm

  # 限制题目数量
  python eval/main.py --agents nutrimaster --max-questions 5

  # 指定版本
  python eval/main.py --agents llm --version v4
"""

import argparse
import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import dotenv

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv()

from eval.datamanager.notion_storage import NotionStorage
from eval.datamanager.local_storage import LocalStorage
from eval.agents.llm_agent import LLMAgent
from eval.agents.nutrimaster_agent import NutriMasterAgent
from eval.agents.evomaster_agent import EvoMasterAgent
from eval.judge.llm_judge import LLMJudge
from eval.metrics.stats import calc_stats, print_stats


# ===== 配置 =====

QUESTION_DB_ID = os.getenv("QUESTION_DB_ID", "e755b041d920410fa6dd3aa88c421879")
RESULT_DB_ID = os.getenv("RESULT_DB_ID", "c7b1b42c0ac14b5f883725f75860860e")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.gpugeek.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "Vendor2/Gemini-3.1-pro")

# LLM Agent 配置
LLM_AGENTS = [
    {"id": "Vendor2/Claude-4.5-Sonnet", "short": "Claude-4.5-Sonnet"},
    {"id": "Vendor2/GPT-5.4", "short": "GPT-5.4"},
]

# NutriMaster 配置
NUTRIMASTER_NAME = os.getenv("NUTRIMASTER_AGENT_NAME", "NutriMaster")
NUTRIMASTER_USE_DEPTH = os.getenv("NUTRIMASTER_USE_DEPTH", "1").lower() in {"1", "true", "yes"}

# EvoMaster 配置
EVOMASTER_PLAYGROUND = os.getenv("EVOMASTER_PLAYGROUND", "fs_mv")
EVOMASTER_CONFIG = os.getenv("EVOMASTER_CONFIG", "")
EVOMASTER_TIMEOUT = int(os.getenv("EVOMASTER_TIMEOUT", "600"))


def create_agent(agent_type: str, agent_config: dict = None):
    """创建 Agent 实例"""
    if agent_type == "llm":
        if not agent_config:
            raise ValueError("LLM agent 需要提供 agent_config")
        return LLMAgent(
            model_id=agent_config["id"],
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
        )
    elif agent_type == "nutrimaster":
        return NutriMasterAgent(use_depth=NUTRIMASTER_USE_DEPTH)
    elif agent_type == "evomaster":
        return EvoMasterAgent(
            playground=EVOMASTER_PLAYGROUND,
            config=EVOMASTER_CONFIG,
            timeout=EVOMASTER_TIMEOUT,
        )
    else:
        raise ValueError(f"未知的 agent 类型: {agent_type}")


async def run_eval(
    questions: list[dict[str, Any]],
    agent: Any,
    judge: LLMJudge,
    version: str,
    agent_concurrency: int = 3,
    judge_concurrency: int = 3,
) -> list[dict[str, Any]]:
    """运行评测"""
    print(f"\n{'='*60}")
    print(f"开始评测")
    print(f"  题目数: {len(questions)}")
    print(f"  Agent: {agent.name}")
    print(f"  版本: {version}")
    print(f"  并发数: Agent={agent_concurrency}, Judge={judge_concurrency}")
    print(f"{'='*60}\n")

    # 第一步: Agent 回答问题（并发）
    print(f"[1/2] Agent 回答问题...")
    agent_sem = asyncio.Semaphore(agent_concurrency)
    answer_tasks = [answer_question(q, agent, agent_sem) for q in questions]
    answers = await asyncio.gather(*answer_tasks)

    # 第二步: Judge 评分（并发）
    print(f"\n[2/2] Judge 评分...")
    judge_sem = asyncio.Semaphore(judge_concurrency)
    judge_tasks = [
        judge_answer(q, ans, judge, agent.name, version, judge_sem)
        for q, ans in zip(questions, answers)
    ]
    results = await asyncio.gather(*judge_tasks)

    return results


async def answer_question(question: dict[str, Any], agent: Any, sem: asyncio.Semaphore) -> dict[str, Any]:
    """Agent 回答单个问题"""
    async with sem:
        q_id = question.get("编号", 0)
        q_text = question.get("正文", "")

        start = time.time()
        result = await agent.answer(q_text)
        duration = time.time() - start

        if result["ok"]:
            print(f"  ✓ 题目 {q_id} - {duration:.2f}s")
        else:
            print(f"  ✗ 题目 {q_id} - 失败: {result['error']}")

        return {
            "question": question,
            "answer": result["output"],
            "error": result["error"],
            "duration": duration,
        }


async def judge_answer(
    question: dict[str, Any],
    answer_result: dict[str, Any],
    judge: LLMJudge,
    agent_name: str,
    version: str,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    """Judge 评分单个答案"""
    async with sem:
        q_id = question.get("编号", 0)
        q_text = question.get("正文", "")
        rubrics = question.get("采分点", [])
        answer = answer_result["answer"]

        # 如果 Agent 失败，直接返回 0 分
        if answer_result["error"]:
            return {
                "题目编号": q_id,
                "Agent名称": agent_name,
                "版本": version,
                "答案": "",
                "总分": 0.0,
                "满分": sum(r.get("满分", 0) for r in rubrics),
                "评分详情": f"Agent 失败: {answer_result['error']}",
                "耗时": answer_result["duration"],
            }

        # Judge 评分
        judge_result = await judge.judge(q_text, answer, rubrics)

        if judge_result["ok"]:
            print(f"  ✓ 题目 {q_id} - {judge_result['总分']:.2f}/{judge_result['满分']:.2f}")
        else:
            print(f"  ✗ 题目 {q_id} - 评分失败: {judge_result['error']}")

        return {
            "题目编号": q_id,
            "Agent名称": agent_name,
            "版本": version,
            "答案": answer,
            "总分": judge_result["总分"],
            "满分": judge_result["满分"],
            "评分详情": judge_result["评分详情"],
            "耗时": answer_result["duration"],
        }


def save_results(results: list[dict[str, Any]], agent_name: str, version: str):
    """保存结果到本地和 Notion"""
    if not results:
        return

    # 1. 保存到本地
    today = datetime.now().strftime("%Y-%m-%d")
    local_dir = Path(__file__).parent / "data" / today / agent_name
    local_dir.mkdir(parents=True, exist_ok=True)
    local_file = local_dir / f"{version}.jsonl"

    LocalStorage.save_results(str(local_file), results)
    print(f"\n✓ 已保存到本地: {local_file}")

    # 2. 保存到 Notion
    try:
        storage = NotionStorage()
        for result in results:
            storage.save_result(RESULT_DB_ID, result)
        print(f"✓ 已保存到 Notion: {len(results)} 条结果")
    except Exception as e:
        print(f"✗ 保存到 Notion 失败: {e}")


async def main():
    parser = argparse.ArgumentParser(description="NutriBench 评测")
    parser.add_argument(
        "--agents",
        default=os.getenv("EVAL_AGENTS", "llm,nutrimaster"),
        help="要评测的 Agent（逗号分隔），可选: llm,nutrimaster,evomaster",
    )
    parser.add_argument("--version", default=os.getenv("EVAL_VERSION", "v3"), help="评测版本")
    parser.add_argument(
        "--max-questions",
        type=int,
        default=int(os.getenv("MAX_QUESTIONS", "0")),
        help="最大题目数（0=全部）",
    )
    parser.add_argument(
        "--agent-concurrency",
        type=int,
        default=int(os.getenv("AGENT_CONCURRENCY", "3")),
        help="Agent 并发数",
    )
    parser.add_argument(
        "--judge-concurrency",
        type=int,
        default=int(os.getenv("JUDGE_CONCURRENCY", "3")),
        help="Judge 并发数",
    )

    args = parser.parse_args()

    # 解析要评测的 Agent
    agent_types = [a.strip().lower() for a in args.agents.split(",") if a.strip()]
    if not agent_types:
        print("错误: 没有指定要评测的 Agent")
        return

    # 加载题目
    print("加载题目...")
    storage = NotionStorage()
    questions = storage.load_questions(
        database_id=QUESTION_DB_ID,
        max_questions=args.max_questions,
    )
    print(f"加载了 {len(questions)} 道题目")

    # 创建 Judge
    judge = LLMJudge(model=JUDGE_MODEL, base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

    try:
        # 对每个 Agent 进行评测
        for agent_type in agent_types:
            # 如果是 llm，需要对每个模型都评测
            if agent_type == "llm":
                for llm_config in LLM_AGENTS:
                    agent = create_agent("llm", llm_config)
                    try:
                        results = await run_eval(
                            questions=questions,
                            agent=agent,
                            judge=judge,
                            version=args.version,
                            agent_concurrency=args.agent_concurrency,
                            judge_concurrency=args.judge_concurrency,
                        )

                        stats = calc_stats(results)
                        print_stats(agent.name, args.version, stats)

                        save_results(results, agent.name, args.version)

                    finally:
                        if hasattr(agent, "close"):
                            await agent.close()
            else:
                agent = create_agent(agent_type)
                try:
                    results = await run_eval(
                        questions=questions,
                        agent=agent,
                        judge=judge,
                        version=args.version,
                        agent_concurrency=args.agent_concurrency,
                        judge_concurrency=args.judge_concurrency,
                    )

                    stats = calc_stats(results)
                    print_stats(agent.name, args.version, stats)

                    save_results(results, agent.name, args.version)

                finally:
                    if hasattr(agent, "close"):
                        await agent.close()

    finally:
        await judge.close()


if __name__ == "__main__":
    asyncio.run(main())
