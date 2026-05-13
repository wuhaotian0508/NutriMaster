from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nutrimaster.extraction.config import INPUT_DIR, MAX_WORKERS, ensure_dirs
from nutrimaster.extraction.config import EXTRACTOR_MODEL
from nutrimaster.extraction.pipeline import (
    resolve_test_files,
    run_pipeline_batch,
    save_token_report,
)
from nutrimaster.extraction.token_tracker import TokenTracker


@dataclass(frozen=True)
class ExtractionRunResult:
    """提取管线单次运行的结果数据类。

    以不可变 dataclass 形式存储一次批量提取运行的汇总信息，
    供 CLI 和 Admin 管理面板使用。

    Attributes:
        files: 本次运行处理的所有文件名列表
        processed: 成功处理（提取+验证）的论文数量
        failed: 处理失败的文件名列表
        skipped: 被跳过的文件名列表（如已存在结果）
        stopped: 是否因外部停止请求而提前终止
        token_report: token 用量报告的保存路径，无报告时为 None
    """
    files: list[str]
    processed: int
    failed: list[str]
    skipped: list[str]
    stopped: bool
    token_report: str | None


class ExtractionService:
    """面向用户的 Markdown 到语料库提取服务边界层。

    封装了提取管线的核心流程，为 CLI 命令行工具和 Admin 管理面板
    提供统一的调用接口。负责文件发现、管线调度和结果汇总。
    """

    def __init__(self, *, input_dir: Path | None = None):
        """初始化提取服务。

        Args:
            input_dir: 输入目录路径，包含待处理的 Markdown 文件。
                      若未指定则使用配置中的默认 INPUT_DIR。
        """
        self.input_dir = Path(input_dir or INPUT_DIR)

    def list_inputs(self) -> list[str]:
        """列出输入目录中所有待处理的 Markdown 文件。

        扫描 input_dir 下所有 .md 文件，按文件名排序返回。

        Returns:
            list[str]: 排序后的 Markdown 文件名列表，目录不存在时返回空列表
        """
        if not self.input_dir.exists():
            return []
        return sorted(path.name for path in self.input_dir.glob("*.md"))

    def run(
        self,
        *,
        test: str | None = None,
        workers: int | None = None,
        stop_requested=None,
        on_paper_start=None,
        on_paper_done=None,
        report_prefix: str = "extract",
    ) -> ExtractionRunResult:
        """执行批量提取管线。

        完整流程：创建必要目录 → 发现输入文件 → 测试模式筛选 →
        调用 run_pipeline_batch 批量处理 → 保存 token 用量报告 → 返回结果。

        Args:
            test: 测试模式参数，可以是数字索引（如 "1"）或文件名模式（如 "Butelli"）。
                 为 None 时处理全部文件。
            workers: 并行工作线程数，为 None 时使用配置中的 MAX_WORKERS。
            stop_requested: 可选的停止检查回调函数，返回 True 时提前终止处理。
                          签名: () -> bool
            on_paper_start: 论文开始处理时的回调函数。
                          签名: (filename, index, total, is_parallel) -> None
            on_paper_done: 论文处理完成时的回调函数。
                         签名: (filename, result, done_count, total, is_parallel) -> None
            report_prefix: token 用量报告文件名前缀，默认 "extract"。

        Returns:
            ExtractionRunResult: 包含处理文件列表、成功/失败/跳过数量等汇总信息
        """
        ensure_dirs()
        files = self.list_inputs()
        if test:
            files = resolve_test_files(files, test)
        tracker = TokenTracker(model=EXTRACTOR_MODEL or "unknown")
        result = run_pipeline_batch(
            files,
            input_dir=self.input_dir,
            workers=workers or MAX_WORKERS,
            tracker=tracker,
            stop_requested=stop_requested,
            on_paper_start=on_paper_start,
            on_paper_done=on_paper_done,
        )
        token_report = save_token_report(tracker, report_prefix)
        return ExtractionRunResult(
            files=files,
            processed=len(result.get("all_reports", [])),
            failed=result.get("failed_files", []),
            skipped=result.get("skipped_files", []),
            stopped=bool(result.get("stopped", False)),
            token_report=token_report,
        )
