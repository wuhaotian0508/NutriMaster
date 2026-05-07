"""
RunManager - 评测运行管理器

功能:
- 并行控制: 限制并发数，避免 API 限流
- 重试机制: 自动重试失败的任务
- 断点续传: 保存检查点，支持中断后恢复
- 进度追踪: 显示进度条和实时统计
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

try:
    from tqdm.asyncio import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


T = TypeVar("T")


class RunManager:
    """评测运行管理器"""

    def __init__(
        self,
        max_concurrency: int = 3,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        checkpoint_dir: str = ".eval_checkpoints",
        auto_save_interval: int = 5,
        enable_progress: bool = True,
    ):
        """
        Args:
            max_concurrency: 最大并发数
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            checkpoint_dir: 检查点保存目录
            auto_save_interval: 自动保存间隔（每 N 个任务）
            enable_progress: 是否显示进度条
        """
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.auto_save_interval = auto_save_interval
        self.enable_progress = enable_progress and HAS_TQDM

        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)

        self.semaphore = asyncio.Semaphore(max_concurrency)

    # ===== 重试机制 =====

    async def run_with_retry(
        self,
        task_fn: Callable,
        *args,
        max_retries: int | None = None,
        retry_delay: float | None = None,
        **kwargs,
    ) -> Any:
        """
        带重试的任务执行

        Args:
            task_fn: 异步任务函数
            max_retries: 最大重试次数（None 使用默认值）
            retry_delay: 重试延迟（None 使用默认值）

        Returns:
            任务执行结果
        """
        max_retries = max_retries if max_retries is not None else self.max_retries
        retry_delay = retry_delay if retry_delay is not None else self.retry_delay

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return await task_fn(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay * (2 ** attempt))  # 指数退避
                    continue
                else:
                    raise last_error

    # ===== 并行执行 =====

    async def run_batch(
        self,
        tasks: list[Callable],
        desc: str = "Processing",
        use_retry: bool = True,
    ) -> list[Any]:
        """
        批量并行执行任务

        Args:
            tasks: 任务列表（异步函数）
            desc: 进度条描述
            use_retry: 是否使用重试机制

        Returns:
            结果列表
        """
        async def _run_task(task_fn):
            async with self.semaphore:
                if use_retry:
                    return await self.run_with_retry(task_fn)
                else:
                    return await task_fn()

        if self.enable_progress:
            return await tqdm.gather(*[_run_task(t) for t in tasks], desc=desc)
        else:
            return await asyncio.gather(*[_run_task(t) for t in tasks])

    # ===== 检查点管理 =====

    def get_checkpoint_path(self, agent_name: str, version: str) -> Path:
        """获取检查点文件路径"""
        safe_name = agent_name.replace("/", "_").replace(" ", "_")
        return self.checkpoint_dir / f"{safe_name}_{version}.jsonl"

    def save_checkpoint(
        self,
        agent_name: str,
        version: str,
        results: list[dict[str, Any]],
        append: bool = True,
    ):
        """
        保存检查点

        Args:
            agent_name: Agent 名称
            version: 版本
            results: 结果列表
            append: 是否追加模式（True=追加，False=覆盖）
        """
        checkpoint_file = self.get_checkpoint_path(agent_name, version)
        mode = "a" if append else "w"

        with open(checkpoint_file, mode, encoding="utf-8") as f:
            for result in results:
                # 添加时间戳
                if "时间戳" not in result:
                    result["时间戳"] = datetime.now().isoformat()
                f.write(json.dumps(result, ensure_ascii=False) + "\n")

    def load_checkpoint(
        self,
        agent_name: str,
        version: str,
    ) -> dict[int, dict[str, Any]]:
        """
        加载检查点

        Returns:
            {题目编号: 结果} 字典
        """
        checkpoint_file = self.get_checkpoint_path(agent_name, version)
        if not checkpoint_file.exists():
            return {}

        completed = {}
        with open(checkpoint_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                result = json.loads(line)
                q_id = result.get("题目编号")
                if q_id is not None:
                    # 保留最新的结果（如果有重复）
                    completed[q_id] = result

        return completed

    def clear_checkpoint(self, agent_name: str, version: str):
        """清除检查点"""
        checkpoint_file = self.get_checkpoint_path(agent_name, version)
        if checkpoint_file.exists():
            checkpoint_file.unlink()

    def get_checkpoint_status(
        self,
        agent_name: str,
        version: str,
    ) -> dict[str, Any]:
        """
        获取检查点状态

        Returns:
            {
                "总题数": 100,
                "已完成": 45,
                "成功": 43,
                "失败": 2,
                "完成率": "45.00%",
            }
        """
        completed = self.load_checkpoint(agent_name, version)

        if not completed:
            return {
                "总题数": 0,
                "已完成": 0,
                "成功": 0,
                "失败": 0,
                "完成率": "0.00%",
            }

        total = len(completed)
        failed = sum(
            1 for r in completed.values()
            if r.get("error") or r.get("总分", 0) == 0
        )
        success = total - failed

        return {
            "总题数": total,
            "已完成": total,
            "成功": success,
            "失败": failed,
            "完成率": f"{100.0:.2f}%",
        }

    # ===== 断点续传 =====

    def filter_remaining_questions(
        self,
        questions: list[dict[str, Any]],
        completed: dict[int, dict[str, Any]],
        retry_failed: bool = False,
    ) -> list[dict[str, Any]]:
        """
        过滤出需要运行的题目

        Args:
            questions: 所有题目
            completed: 已完成的题目 {题目编号: 结果}
            retry_failed: 是否重跑失败的题目

        Returns:
            需要运行的题目列表
        """
        remaining = []

        for q in questions:
            q_id = q.get("编号")

            # 未完成的题目
            if q_id not in completed:
                remaining.append(q)
                continue

            # 重跑失败的题目
            if retry_failed:
                result = completed[q_id]
                # 判断失败：有 error 或总分为 0
                if result.get("error") or result.get("总分", 0) == 0:
                    remaining.append(q)

        return remaining

    async def run_with_resume(
        self,
        agent_name: str,
        version: str,
        questions: list[dict[str, Any]],
        eval_fn: Callable,
        resume: bool = True,
        retry_failed: bool = False,
    ) -> list[dict[str, Any]]:
        """
        带断点续传的评测运行

        Args:
            agent_name: Agent 名称
            version: 版本
            questions: 所有题目
            eval_fn: 评测函数 async def eval_fn(question) -> result
            resume: 是否从检查点恢复
            retry_failed: 是否重跑失败的题目

        Returns:
            所有结果列表
        """
        # 1. 加载检查点
        completed = {}
        if resume:
            completed = self.load_checkpoint(agent_name, version)
            if completed:
                print(f"📂 加载检查点: 已完成 {len(completed)} 题")

        # 2. 过滤剩余题目
        remaining = self.filter_remaining_questions(
            questions, completed, retry_failed
        )

        if not remaining:
            print("✅ 所有题目已完成")
            return list(completed.values())

        print(f"🔄 剩余 {len(remaining)} 题待评测")

        # 3. 运行评测（自动保存）
        new_results = []
        for i, q in enumerate(remaining, 1):
            try:
                result = await self.run_with_retry(eval_fn, q)
                new_results.append(result)

                # 自动保存
                if i % self.auto_save_interval == 0:
                    self.save_checkpoint(agent_name, version, new_results, append=True)
                    new_results = []
                    print(f"💾 自动保存检查点 ({i}/{len(remaining)})")

            except Exception as e:
                print(f"❌ 题目 {q.get('编号')} 失败: {e}")
                # 保存失败记录
                error_result = {
                    "题目编号": q.get("编号"),
                    "Agent名称": agent_name,
                    "版本": version,
                    "答案": "",
                    "总分": 0.0,
                    "满分": sum(r.get("满分", 0) for r in q.get("采分点", [])),
                    "error": str(e),
                }
                new_results.append(error_result)

        # 4. 保存剩余结果
        if new_results:
            self.save_checkpoint(agent_name, version, new_results, append=True)
            print(f"💾 保存最终检查点")

        # 5. 返回所有结果
        all_completed = self.load_checkpoint(agent_name, version)
        return list(all_completed.values())

    # ===== 工具方法 =====

    def export_checkpoint(
        self,
        agent_name: str,
        version: str,
        output_path: str,
    ):
        """导出检查点到指定文件"""
        checkpoint_file = self.get_checkpoint_path(agent_name, version)
        if not checkpoint_file.exists():
            print(f"❌ 检查点不存在: {checkpoint_file}")
            return

        import shutil
        shutil.copy(checkpoint_file, output_path)
        print(f"✅ 检查点已导出到: {output_path}")

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """列出所有检查点"""
        checkpoints = []
        for file in self.checkpoint_dir.glob("*.jsonl"):
            # 解析文件名: {agent}_{version}.jsonl
            name = file.stem
            parts = name.rsplit("_", 1)
            if len(parts) == 2:
                agent_name, version = parts
            else:
                agent_name, version = name, "unknown"

            # 统计信息
            completed = self.load_checkpoint(agent_name, version)
            status = self.get_checkpoint_status(agent_name, version)

            checkpoints.append({
                "agent": agent_name,
                "version": version,
                "file": str(file),
                "size": file.stat().st_size,
                "modified": datetime.fromtimestamp(file.stat().st_mtime).isoformat(),
                **status,
            })

        return checkpoints
