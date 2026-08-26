"""
pipeline.py — 论文级并行处理的编排器。

每篇论文经过以下流程：
    API #1: extract_all_genes  → 提取 Title/Journal/DOI + 基因数组 + gene_dict
    API #2+: verify_all_genes  → 验证 + 修正（每 10 个基因一批）

论文之间使用 ThreadPoolExecutor 进行并行处理。

用法：
    python -m extractor.pipeline               # 完整管线
    python -m extractor.pipeline --test 1      # 测试模式：第一个文件
    python -m extractor.pipeline --test name   # 测试模式：匹配文件名
    python -m extractor.pipeline --workers 5   # 自定义并行数
"""

import argparse
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .config import (
    EXTRACTOR_MODEL, INPUT_DIR, TOKEN_USAGE_DIR, MAX_WORKERS,
    ensure_dirs,
)
from .extract import extract_paper
from .utils import GENE_ARRAY_KEY_NAMES
from .verify import verify_paper
from .token_tracker import TokenTracker


def collect_paper_result(filename: str, result: dict, all_reports: list,
                         failed_files: list, skipped_files: list,
                         *, retain_report: bool = True):
    """把单篇论文的处理结果分桶收集。

    根据 result 中的 status 字段，将结果分类到三个列表中：
    - "processed": 成功处理，将报告添加到 all_reports
    - "skipped": 被跳过，将文件名添加到 skipped_files
    - 其他: 处理失败，将文件名添加到 failed_files

    Args:
        filename: 论文文件名
        result: 处理结果字典，包含 status 和可选的 report 字段
        all_reports: 成功处理的报告收集列表（原地修改）
        failed_files: 失败文件名收集列表（原地修改）
        skipped_files: 跳过文件名收集列表（原地修改）
    """
    status = result.get("status", "failed")
    if status == "processed":
        report = result.get("report")
        if report and retain_report:
            all_reports.append(report)
    elif status == "skipped":
        skipped_files.append(filename)
    else:
        failed_files.append(filename)


def resolve_test_files(files: list, test_index: str) -> list:
    """测试模式下筛选文件。

    支持两种匹配方式：
    - 数字索引：--test 1 → 选择第 1 个文件（1-based）
    - 文件名匹配：--test Butelli → 先精确匹配，再模糊匹配含 "Butelli" 的文件

    Args:
        files: 所有可用的 Markdown 文件名列表
        test_index: 测试模式参数，数字字符串或文件名模式

    Returns:
        list: 匹配到的文件名列表（通常只有 1 个），无匹配时返回空列表
    """
    if test_index.isdigit():
        idx = int(test_index) - 1
        if 0 <= idx < len(files):
            print(f"🧪 Test mode: file #{idx + 1} → {files[idx]}")
            return [files[idx]]
        print(f"❌ Index {idx + 1} out of range ({len(files)} files)")
        return []
    target = test_index if test_index.endswith(".md") else test_index + ".md"
    matched = [f for f in files if f == target]
    if matched:
        print(f"🧪 Test mode: exact match → {matched[0]}")
        return matched
    matched = [f for f in files if test_index in f]
    if matched:
        print(f"🧪 Test mode: fuzzy match → {matched[0]}")
        return [matched[0]]
    print(f"❌ No match for '{test_index}' ({len(files)} files)")
    return []


def _print_paper_result(stem: str, result: dict):
    """打印单篇论文的处理结果摘要。

    顺序和并行模式共用此函数输出统一格式的结果。
    显示 fidelity 准确率（SUPPORTED/总字段）和 corrections 修正数。

    Args:
        stem: 论文文件名 stem（不含扩展名）
        result: 处理结果字典，包含 status 和 report 字段
    """
    status = result.get("status")
    if status == "processed":
        report_data = result["report"]
        s = report_data["summary"]
        if s["total_fields"] > 0:
            print(f"  📈 {stem}: fidelity {s['supported']}/{s['total_fields']} "
                  f"({s['supported'] / s['total_fields'] * 100:.0f}%) | "
                  f"corrections {s['total_corrections']}")
    elif status == "skipped":
        print(f"  ⏭️  {stem}: skipped")


def process_one_paper(md_path: Path, stem: str, tracker: TokenTracker):
    """处理单篇论文：提取 + 验证（线程安全）。

    流程：调用 extract_paper() 提取基因 → 调用 verify_paper() 验证 → 返回结果。
    在 ThreadPoolExecutor 中并行调用，每篇论文独立处理，互不影响。

    Args:
        md_path: 论文 Markdown 文件的完整路径
        stem: 论文文件名 stem（不含扩展名）
        tracker: token 用量追踪器

    Returns:
        dict: 处理结果，包含 status（"processed"/"failed"/"skipped"）和 report 字段
    """
    try:
        extraction, gene_dict = extract_paper(md_path, tracker)

        if extraction is None:
            print(f"  ❌ Extraction failed, skip verify: {stem}")
            return {"status": "failed", "report": None}

        total_genes = sum(
            len(extraction.get(k, []))
            for k in GENE_ARRAY_KEY_NAMES
        )
        print(f"  📊 Extracted {total_genes} genes, dict: {gene_dict}")

        report = verify_paper(md_path, extraction, stem, tracker)
        if report is None:
            return {"status": "failed", "report": None}
        if report.get("status") == "skipped":
            return report
        return {"status": "processed", "report": report}

    except MemoryError:
        # Never downgrade allocator exhaustion to one failed paper and then
        # continue allocating for the rest of the corpus.
        raise
    except Exception as e:
        print(f"  ❌ Error processing {stem}: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "failed", "report": None}


def print_verify_summary(all_reports: list):
    """打印所有论文的验证汇总统计。

    统计并输出全部论文的总字段数、SUPPORTED/UNSUPPORTED/UNCERTAIN
    分布、总修正数和整体 fidelity 准确率。

    Args:
        all_reports: 所有成功处理的验证报告列表，每项包含 summary 字段
    """
    if not all_reports:
        return

    print(f"\n{'=' * 60}")
    print(f"📊 Verification Summary")
    print(f"{'=' * 60}")

    total_files = len(all_reports)
    total_fields = sum(r["summary"]["total_fields"] for r in all_reports)
    total_supported = sum(r["summary"]["supported"] for r in all_reports)
    total_unsupported = sum(r["summary"]["unsupported"] for r in all_reports)
    total_uncertain = sum(r["summary"]["uncertain"] for r in all_reports)
    total_corrections = sum(r["summary"]["total_corrections"] for r in all_reports)

    print(f"  Files verified: {total_files}")
    print(f"  Fields checked: {total_fields}")
    print(f"  ✅ SUPPORTED:   {total_supported}")
    print(f"  ❓ UNCERTAIN:   {total_uncertain}")
    print(f"  ❌ UNSUPPORTED: {total_unsupported}")
    print(f"  🔧 Corrections: {total_corrections}")

    if total_fields > 0:
        accuracy = total_supported / total_fields * 100
        print(f"  📈 Fidelity:    {accuracy:.1f}%")


def save_token_report(
    tracker: TokenTracker,
    prefix: str = "pipeline",
    output_dir: Optional[Path] = None,
) -> Optional[str]:
    """持久化当前 token 追踪器的用量报告。

    将追踪器中记录的所有 API 调用 token 用量保存为带时间戳的 JSON 文件。

    Args:
        tracker: token 用量追踪器实例
        prefix: 报告文件名前缀，默认 "pipeline"
        output_dir: 报告输出目录，为 None 时使用配置中的 TOKEN_USAGE_DIR

    Returns:
        str: 保存的报告文件路径，无数据时返回 None
    """
    if not tracker or not tracker.calls:
        return None

    report_dir = Path(output_dir or TOKEN_USAGE_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"{prefix}-{timestamp}.json"
    tracker.save_report(str(report_path))
    return str(report_path)


def run_pipeline_batch(
    files: list[str],
    *,
    input_dir: Optional[Path] = None,
    workers: int = MAX_WORKERS,
    tracker: Optional[TokenTracker] = None,
    stop_requested: Optional[Callable[[], bool]] = None,
    on_paper_start: Optional[Callable[[str, int, int, bool], None]] = None,
    on_paper_done: Optional[Callable[[str, dict, int, int, bool], None]] = None,
    retain_reports: bool = True,
) -> dict:
    """执行一批论文的提取管线编排（CLI 和 Admin 共用核心）。

    这是 CLI 命令行和 /admin 管理面板共享的可复用批处理核心：
    - CLI 用于常规批量处理 + 终端输出
    - Admin 用于 SSE 进度推送、停止检查和 token 追踪

    Web 层负责鉴权/UI/索引重建；本函数只负责论文处理循环和 token 报告。

    根据文件数和 workers 自动选择顺序或并行模式：
    - 单文件或 workers=1 → 顺序处理
    - 多文件且 workers>1 → ThreadPoolExecutor 并行处理

    Args:
        files: 待处理的 Markdown 文件名列表
        input_dir: 输入目录路径，为 None 时使用配置中的 INPUT_DIR
        workers: 并行工作线程数，默认使用 MAX_WORKERS
        tracker: token 用量追踪器，为 None 时自动创建
        stop_requested: 可选的停止检查回调，返回 True 时提前终止
        on_paper_start: 论文开始处理时的回调
        on_paper_done: 论文处理完成时的回调
        retain_reports: 是否在内存中保留所有完整报告。Admin 进程应关闭；
            CLI 默认保留以生成汇总。

    Returns:
        dict: 包含以下键的结果字典：
            - tracker: TokenTracker 实例
            - all_reports: 成功处理的验证报告列表
            - failed_files: 失败的文件名列表
            - skipped_files: 跳过的文件名列表
            - stopped: 是否被提前停止
            - submitted: 已提交/处理的文件数
            - done: 已完成的文件数
            - total: 总文件数
    """
    input_dir = Path(input_dir or INPUT_DIR)
    tracker = tracker or TokenTracker(model=EXTRACTOR_MODEL)
    total = len(files)
    all_reports = []
    failed_files = []
    skipped_files = []
    stopped = False
    submitted = 0
    done_count = 0
    is_parallel = total > 1 and workers > 1

    if not is_parallel:
        for i, filename in enumerate(files):
            if stop_requested and stop_requested():
                stopped = True
                break

            if on_paper_start:
                on_paper_start(filename, i, total, False)

            md_path = input_dir / filename
            stem = Path(filename).stem
            result = process_one_paper(md_path, stem, tracker)
            collect_paper_result(
                filename,
                result,
                all_reports,
                failed_files,
                skipped_files,
                retain_report=retain_reports,
            )

            done_count = i + 1
            if on_paper_done:
                on_paper_done(filename, result, done_count, total, False)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_file = {}
            remaining_files = iter(files)

            def submit_next() -> bool:
                """Keep at most ``workers`` papers in flight at any time."""

                nonlocal stopped, submitted
                if stop_requested and stop_requested():
                    stopped = True
                    return False

                try:
                    filename = next(remaining_files)
                except StopIteration:
                    return False

                if on_paper_start:
                    on_paper_start(filename, submitted, total, True)

                md_path = input_dir / filename
                stem = Path(filename).stem
                future = pool.submit(process_one_paper, md_path, stem, tracker)
                future_to_file[future] = filename
                submitted += 1
                return True

            for _ in range(min(workers, total)):
                if not submit_next():
                    break

            while future_to_file:
                completed, _pending = wait(
                    tuple(future_to_file),
                    return_when=FIRST_COMPLETED,
                )
                completed_results = []
                try:
                    for future in completed:
                        filename = future_to_file.pop(future)
                        try:
                            result = future.result()
                        except MemoryError:
                            # Do not submit replacement work after any worker
                            # reports allocator pressure.
                            raise
                        except Exception as e:
                            print(f"  ❌ {filename}: {e}")
                            result = {"status": "failed", "report": None}
                        completed_results.append((filename, result))
                except MemoryError:
                    for pending in future_to_file:
                        pending.cancel()
                    raise

                for filename, result in completed_results:
                    collect_paper_result(
                        filename,
                        result,
                        all_reports,
                        failed_files,
                        skipped_files,
                        retain_report=retain_reports,
                    )

                    done_count += 1
                    if on_paper_done:
                        on_paper_done(filename, result, done_count, total, True)

                for _ in completed_results:
                    if not submit_next():
                        break

    return {
        "tracker": tracker,
        "all_reports": all_reports,
        "failed_files": failed_files,
        "skipped_files": skipped_files,
        "stopped": stopped,
        "submitted": submitted if is_parallel else done_count,
        "done": done_count,
        "total": total,
    }


def main():
    """提取管线主函数：解析命令行参数 → 发现文件 → 顺序/并行处理 → 汇总输出。

    支持的参数：
    - --test: 测试模式，指定文件索引或文件名模式
    - --workers: 并行工作线程数

    也支持环境变量 TEST_MODE=1 和 TEST_INDEX 进入测试模式。
    """
    parser = argparse.ArgumentParser(description="NutriMaster Extraction Pipeline")
    parser.add_argument("--test", type=str, default=None,
                        help="Test mode: file index (1-based) or filename pattern")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"Parallel workers (default: {MAX_WORKERS})")
    args = parser.parse_args()

    ensure_dirs()

    # ── Pipeline mode ─────────────────────────────────────────────────────────
    print("═" * 60)
    print("🚀 NutriMaster Pipeline")
    print(f"   Model:     {EXTRACTOR_MODEL}")
    print(f"   Input:     {INPUT_DIR}")
    print(f"   Workers:   {args.workers}")
    print("═" * 60)

    input_dir = Path(INPUT_DIR)
    if not input_dir.exists():
        print(f"❌ Input dir does not exist: {input_dir}")
        return

    files = sorted([f for f in os.listdir(input_dir) if f.endswith(".md")])
    print(f"📂 Found {len(files)} files")

    # Test mode
    if args.test is not None:
        files = resolve_test_files(files, args.test)
        if not files:
            return
    elif os.getenv("TEST_MODE") == "1":
        test_index = os.getenv("TEST_INDEX", "1")
        files = resolve_test_files(files, test_index)
        if not files:
            return

    workers = args.workers
    is_parallel = len(files) > 1 and workers > 1
    tracker = TokenTracker(model=EXTRACTOR_MODEL)

    def _on_paper_start(filename: str, index: int, total: int, parallel: bool):
        """论文开始处理时的 CLI 回调，顺序模式下打印单篇头部分隔线。

        Args:
            filename: 当前处理的文件名
            index: 当前文件的索引（0-based）
            total: 总文件数
            parallel: 是否为并行模式
        """
        # CLI 只在顺序模式下打印单篇头部，避免并行日志互相打乱。
        if not parallel:
            print(f"\n{'━' * 60}")
            print(f"📄 [{index + 1}/{total}] {filename}")
            print(f"{'━' * 60}")

    def _on_paper_done(filename: str, result: dict, done: int, total: int, parallel: bool):
        """论文处理完成时的 CLI 回调，打印结果摘要。

        Args:
            filename: 已处理的文件名
            result: 处理结果字典
            done: 已完成的文件数
            total: 总文件数
            parallel: 是否为并行模式
        """
        stem = Path(filename).stem
        _print_paper_result(stem, result)

    if is_parallel:
        print(f"\n🔄 Processing {len(files)} papers with {workers} workers...\n")

    run_result = run_pipeline_batch(
        files,
        input_dir=input_dir,
        workers=workers,
        tracker=tracker,
        on_paper_start=_on_paper_start,
        on_paper_done=_on_paper_done,
    )
    all_reports = run_result["all_reports"]
    failed_files = run_result["failed_files"]
    skipped_files = run_result["skipped_files"]

    # ── Summary ───────────────────────────────────────────────────────────────
    if all_reports:
        print_verify_summary(all_reports)

    tracker.print_summary()
    save_token_report(tracker, "pipeline")

    if failed_files:
        print(f"\n⚠️  {len(failed_files)} files failed: {failed_files}")
        print(f"   Tip: FORCE_RERUN=1 bash src/nutrimaster/extraction/run.sh pipeline")
    if skipped_files:
        print(f"\n⏭️  {len(skipped_files)} files skipped: {skipped_files}")

    print(f"\n✅ Pipeline done! Processed {len(files)}, verified {len(all_reports)}")


if __name__ == "__main__":
    main()
