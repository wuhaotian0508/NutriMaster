"""
NutriBench 评测主脚本

使用示例:
  python -m eval.main pull --after 2026-05-06
  python -m eval.main nutrimaster v3 --resume
  python -m eval.main push --dry-run
"""

import argparse
import asyncio
import os
import sys

from eval.configs import (
    DEFAULT_RESULTS_FILE,
    JUDGE_API_KEY,
    JUDGE_BASE_URL,
    JUDGE_MODEL,
)
from eval.datamanager import load_local_questions, pull_questions, push_results
from eval.judge.llm_judge import LLMJudge
from eval.run_manager import RunManager
from eval.runner import EvaluationRunner


def build_pull_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从 Notion 下载题目到本地")
    parser.add_argument(
        "--after",
        "--question-created-after",
        dest="after",
        help="只下载该日期/时间之后创建的题目，如 2026-05-06（按 Asia/Shanghai）",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=int(os.getenv("MAX_QUESTIONS", "0")),
        help="最大题目数（0=全部）",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="输出题目 JSONL；默认写入 eval/data/questions/questions_*.jsonl",
    )
    return parser


def build_push_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="上传本地 eval 结果到 Notion")
    parser.add_argument(
        "--file",
        "-f",
        default=str(DEFAULT_RESULTS_FILE),
        help="要上传的结果 JSONL，默认 eval/data/results/latest.jsonl",
    )
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写入 Notion")
    return parser


def build_eval_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NutriBench 评测",
        epilog=(
            "示例: python -m eval.main pull --after 2026-05-06; "
            "python -m eval.main nutrimaster v3 --resume; "
            "python -m eval.main push --dry-run"
        ),
    )

    parser.add_argument(
        "agent",
        nargs="?",
        help="Agent 名称（可选位置参数），可选: llm,nutrimaster,evomaster",
    )
    parser.add_argument(
        "version_arg",
        nargs="?",
        help="评测版本（可选位置参数），如: v3, v4",
    )
    parser.add_argument(
        "--agents",
        help="要评测的 Agent（逗号分隔），可选: llm,nutrimaster,evomaster",
    )
    parser.add_argument("--version", help="评测版本")
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
    parser.add_argument(
        "--question-created-after",
        help="只使用本地题目文件中该日期/时间之后创建的题目，如 2026-05-06（按 Asia/Shanghai）",
    )
    parser.add_argument(
        "--questions-file",
        help="本地题目 JSONL；默认 eval/data/questions/latest.jsonl",
    )
    parser.add_argument(
        "--llm-model",
        help="只评测指定 LLM 模型，可用 Vendor2/GPT-5.4、GPT-5.4、gpt5.4；多个用逗号分隔",
    )
    parser.add_argument("--resume", action="store_true", help="从检查点恢复，跳过已完成的题目")
    parser.add_argument("--retry-failed", action="store_true", help="重跑失败的题目（总分=0 或有 error）")
    parser.add_argument("--clean", action="store_true", help="清除检查点，从头开始")
    parser.add_argument("--checkpoint-dir", default=".eval_checkpoints", help="检查点保存目录")
    parser.add_argument("--max-retries", type=int, default=3, help="最大重试次数")
    parser.add_argument("--no-progress", action="store_true", help="禁用进度条")
    return parser


def resolve_agent_types(args: argparse.Namespace) -> list[str]:
    if args.agent:
        agents_str = args.agent
    elif args.agents:
        agents_str = args.agents
    else:
        agents_str = os.getenv("EVAL_AGENTS", "llm,nutrimaster")
    return [agent.strip().lower() for agent in agents_str.split(",") if agent.strip()]


def resolve_version(args: argparse.Namespace) -> str:
    if args.version_arg:
        return args.version_arg
    if args.version:
        return args.version
    return os.getenv("EVAL_VERSION", "v3")


async def main():
    if len(sys.argv) > 1 and sys.argv[1] in {"pull", "sync-questions"}:
        args = build_pull_parser().parse_args(sys.argv[2:])
        pull_questions(
            after=args.after,
            max_questions=args.max_questions,
            output=args.output,
        )
        return

    if len(sys.argv) > 1 and sys.argv[1] in {"push", "upload-results"}:
        args = build_push_parser().parse_args(sys.argv[2:])
        push_results(file=args.file, dry_run=args.dry_run)
        return

    args = build_eval_parser().parse_args()
    agent_types = resolve_agent_types(args)
    version = resolve_version(args)
    if not agent_types:
        print("错误: 没有指定要评测的 Agent")
        return

    print("加载本地题目...")
    try:
        questions, questions_file = load_local_questions(
            questions_file=args.questions_file,
            max_questions=args.max_questions,
            question_created_after=args.question_created_after,
        )
    except FileNotFoundError as e:
        print(f"错误: {e}")
        print("请先运行: python -m eval.main pull --after 2026-05-06")
        return

    print(f"题目文件: {questions_file}")
    if args.question_created_after:
        print(f"题目创建时间过滤: created_time >= {args.question_created_after}")
    print(f"加载了 {len(questions)} 道题目")
    if not questions:
        print("没有可评测的题目")
        return

    judge = LLMJudge(model=JUDGE_MODEL, base_url=JUDGE_BASE_URL, api_key=JUDGE_API_KEY)
    run_manager = RunManager(
        max_concurrency=args.agent_concurrency,
        max_retries=args.max_retries,
        checkpoint_dir=args.checkpoint_dir,
        enable_progress=not args.no_progress,
    )
    runner = EvaluationRunner(
        run_manager=run_manager,
        judge=judge,
        version=version,
        judge_concurrency=args.judge_concurrency,
        resume=args.resume,
        retry_failed=args.retry_failed,
        clean=args.clean,
    )

    print(
        f"并发设置: agent/question={args.agent_concurrency}, judge={args.judge_concurrency}",
        flush=True,
    )

    try:
        await runner.run(
            questions=questions,
            agent_types=agent_types,
            llm_model=args.llm_model,
        )
    finally:
        await judge.close()


if __name__ == "__main__":
    asyncio.run(main())
