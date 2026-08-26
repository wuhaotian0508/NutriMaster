from __future__ import annotations

import asyncio
import queue
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any, AsyncGenerator

from nutrimaster.experiment.crispr import stream_crispr_workflow
from nutrimaster.experiment.gene_validation import (
    extract_transgenic_species_with_llm,
    verify_genes_with_ncbi,
)
from nutrimaster.experiment.sop import format_sops
from nutrimaster.experiment.resource_limits import validate_sop_output


MAX_EXPERIMENT_GOAL_CHARS = 16_000
MAX_EXPERIMENT_GENES = 50
MAX_SELECTED_GENE_NAMES = 50
MAX_RECIPIENT_SPECIES = 20
MAX_GENE_NAME_CHARS = 128
MAX_SPECIES_NAME_CHARS = 256
_EVENT_QUEUE_CAPACITY = 16


class ExperimentInputError(ValueError):
    """Raised when an experiment request exceeds its safe input contract."""


class ExperimentBusyError(RuntimeError):
    """Raised when another memory-sensitive experiment job is still active."""


class ExperimentExecutionGate:
    """Fail-fast process-local gate shared by every online experiment service.

    The synchronous experiment workers cannot be cancelled by cancelling their
    owning coroutine.  A threading lock therefore guards the real worker
    lifetime, including the period after an SSE client disconnects.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def try_acquire(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise ExperimentBusyError("实验服务正忙，请等待当前任务完成后重试")

    def release(self) -> None:
        self._lock.release()

    @contextmanager
    def hold(self) -> Iterator[None]:
        self.try_acquire()
        try:
            yield
        finally:
            self.release()


def _normalize_bounded_text(
    value: Any,
    *,
    field: str,
    max_chars: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ExperimentInputError(f"{field} 必须是字符串")
    if len(value) > max_chars:
        raise ExperimentInputError(f"{field} 过长，最多 {max_chars} 个字符")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ExperimentInputError(f"{field} 不能为空")
    return normalized


def normalize_experiment_goal(value: Any, *, allow_empty: bool = False) -> str:
    """Validate and trim one experiment goal before it reaches an LLM prompt."""
    return _normalize_bounded_text(
        value,
        field="goal",
        max_chars=MAX_EXPERIMENT_GOAL_CHARS,
        allow_empty=allow_empty,
    )


def normalize_selected_gene_names(value: Any) -> list[str] | None:
    """Validate the optional user-selected gene-name list."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ExperimentInputError("selected_gene_names 必须是数组")
    if len(value) > MAX_SELECTED_GENE_NAMES:
        raise ExperimentInputError(
            f"selected_gene_names 最多包含 {MAX_SELECTED_GENE_NAMES} 项"
        )
    return [
        _normalize_bounded_text(
            item,
            field=f"selected_gene_names[{index}]",
            max_chars=MAX_GENE_NAME_CHARS,
        )
        for index, item in enumerate(value)
    ]


def normalize_experiment_genes(
    value: Any,
    *,
    allow_none: bool = False,
    require_nonempty: bool = False,
    require_species: bool = True,
) -> list[dict[str, str]] | None:
    """Validate and reduce gene records to the fields consumed by workflows."""
    if value is None:
        if allow_none:
            return None
        raise ExperimentInputError("genes 必须是数组")
    if not isinstance(value, list):
        raise ExperimentInputError("genes 必须是数组")
    if require_nonempty and not value:
        raise ExperimentInputError("genes 不能为空")
    if len(value) > MAX_EXPERIMENT_GENES:
        raise ExperimentInputError(f"genes 最多包含 {MAX_EXPERIMENT_GENES} 项")

    normalized: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ExperimentInputError(f"genes[{index}] 必须是对象")
        gene = _normalize_bounded_text(
            item.get("gene"),
            field=f"genes[{index}].gene",
            max_chars=MAX_GENE_NAME_CHARS,
        )
        species_value = item.get("species", "")
        species = _normalize_bounded_text(
            species_value,
            field=f"genes[{index}].species",
            max_chars=MAX_SPECIES_NAME_CHARS,
            allow_empty=not require_species,
        )
        normalized.append({"gene": gene, "species": species})
    return normalized


def normalize_recipient_species(value: Any) -> list[str]:
    """Validate a non-empty list of gene-transfer recipient species."""
    if not isinstance(value, list):
        raise ExperimentInputError("species_list 必须是数组")
    if not value:
        raise ExperimentInputError("species_list 不能为空")
    if len(value) > MAX_RECIPIENT_SPECIES:
        raise ExperimentInputError(
            f"species_list 最多包含 {MAX_RECIPIENT_SPECIES} 项"
        )
    return [
        _normalize_bounded_text(
            item,
            field=f"species_list[{index}]",
            max_chars=MAX_SPECIES_NAME_CHARS,
        )
        for index, item in enumerate(value)
    ]


class ExperimentDesignService:
    """CRISPR 实验设计服务的高层封装，供智能体和 Web API 调用。

    提供基因预览、实验运行和工具调用等异步接口，
    将基因提取、NCBI 验证和 SOP 生成等步骤整合为统一的工作流。
    """

    def __init__(self, pipeline_factory=None, *, execution_gate: ExperimentExecutionGate | None = None):
        """初始化实验设计服务。

        Args:
            pipeline_factory: 可选的管线工厂函数，调用后返回 ExperimentPipeline 实例。
                若为 None，则使用默认的 ExperimentPipeline 构造。
        """
        self.pipeline_factory = pipeline_factory
        self.execution_gate = execution_gate or ExperimentExecutionGate()

    async def preview(
        self,
        *,
        goal: str,
        genes: list[dict[str, Any]] | None = None,
        selected_gene_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """预览实验设计方案，提取基因并通过 NCBI 验证。

        根据提供的参数选择不同的基因提取策略：
        - 若直接提供 genes，则使用该列表；
        - 若提供 selected_gene_names，则根据目标和指定基因名提取；
        - 否则由 LLM 自动从目标描述中提取基因。

        最终对提取的基因列表进行 NCBI 数据库验证。

        Args:
            goal: 实验目标描述文本。
            genes: 可选的基因信息字典列表，直接指定待验证的基因。
            selected_gene_names: 可选的基因名称列表，用于从目标中定向提取指定基因。

        Returns:
            list[dict[str, Any]]: 经 NCBI 验证后的基因信息列表。
        """
        goal = normalize_experiment_goal(goal, allow_empty=bool(genes))
        genes = normalize_experiment_genes(
            genes,
            allow_none=True,
            require_species=False,
        )
        selected_gene_names = normalize_selected_gene_names(selected_gene_names)
        def _preview_sync() -> list[dict[str, Any]]:
            # Acquire inside the worker. Cancelling ``asyncio.to_thread`` does
            # not stop that worker, so the gate must outlive the coroutine.
            with self.execution_gate.hold():
                pipeline = self._create_pipeline()
                try:
                    if genes:
                        extracted = genes
                    elif selected_gene_names:
                        extracted = pipeline.extract_selected_genes_with_llm(
                            goal,
                            selected_gene_names,
                        )
                    else:
                        extracted = pipeline.extract_genes_with_llm(goal)
                    extracted = normalize_experiment_genes(
                        extracted,
                        require_nonempty=True,
                        # CRISPR preview has always allowed a known gene without a
                        # species; NCBI verification can return an unverified preview
                        # and let the user supply the species before the run stage.
                        require_species=False,
                    )
                    return verify_genes_with_ncbi(extracted)
                finally:
                    pipeline.cleanup()

        return await asyncio.to_thread(_preview_sync)

    async def run(self, *, genes: list[dict[str, Any]]) -> AsyncGenerator[dict, None]:
        """流式运行完整的 CRISPR 实验设计工作流，逐步 yield progress/result/error 事件。

        在后台线程中执行同步 pipeline，通过 queue 将事件传回主线程 async generator。

        Args:
            genes: 基因信息字典列表。

        Yields:
            dict: pipeline 产生的事件（type: progress / result / error）。
        """
        genes = normalize_experiment_genes(
            genes,
            require_nonempty=True,
            require_species=True,
        )
        assert genes is not None
        self.execution_gate.try_acquire()
        gate_owned_by_caller = True
        try:
            pipeline = self._create_pipeline()
        except BaseException:
            self.execution_gate.release()
            raise
        q: queue.Queue[Any] = queue.Queue(maxsize=_EVENT_QUEUE_CAPACITY)
        stop_event = threading.Event()
        sentinel = object()

        def _enqueue(item: Any) -> bool:
            while not stop_event.is_set():
                try:
                    q.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    continue
            return False

        def _dequeue() -> Any:
            while not stop_event.is_set():
                try:
                    return q.get(timeout=0.1)
                except queue.Empty:
                    continue
            return sentinel

        def _run_in_thread():
            memory_error: MemoryError | None = None
            ordinary_error: Exception | None = None
            try:
                for event in stream_crispr_workflow(pipeline, genes):
                    if event.get("type") == "result":
                        validate_sop_output(event.get("sops", {}))
                    if not _enqueue(event):
                        return
            except MemoryError as exc:
                memory_error = exc
            except Exception as exc:
                ordinary_error = exc
            finally:
                try:
                    try:
                        pipeline.cleanup()
                    except MemoryError as exc:
                        if memory_error is None:
                            memory_error = exc
                    except Exception as exc:
                        if ordinary_error is None:
                            ordinary_error = exc

                    terminal_item: Any = memory_error
                    if terminal_item is None and ordinary_error is not None:
                        try:
                            terminal_item = {
                                "type": "error",
                                "msg": str(ordinary_error),
                            }
                        except MemoryError as exc:
                            terminal_item = exc
                    if terminal_item is not None:
                        _enqueue(terminal_item)
                    _enqueue(sentinel)
                finally:
                    # The coroutine may already be cancelled, but this worker
                    # can still hold downloaded sequences and SOP buffers.
                    self.execution_gate.release()

        thread = threading.Thread(target=_run_in_thread, daemon=True)
        try:
            thread.start()
            gate_owned_by_caller = False
        except MemoryError:
            try:
                pipeline.cleanup()
            except BaseException:
                pass
            self.execution_gate.release()
            gate_owned_by_caller = False
            raise
        except BaseException:
            try:
                pipeline.cleanup()
            finally:
                self.execution_gate.release()
                gate_owned_by_caller = False
            raise

        try:
            while True:
                item = await asyncio.to_thread(_dequeue)
                if item is sentinel:
                    break
                if isinstance(item, MemoryError):
                    raise item
                yield item
        finally:
            stop_event.set()
            if gate_owned_by_caller:
                self.execution_gate.release()

    async def tool_call(
        self,
        *,
        goal: str,
        genes: list[dict[str, Any]] | None = None,
        output: str = "advice",
        confirmed: bool = False,
    ) -> str:
        """智能体工具调用入口，根据参数返回实验预览或完整 SOP。

        首先执行基因预览，然后根据 output 和 confirmed 参数决定返回内容：
        - 若 output 不为 "full_sop" 或 confirmed 为 False，返回简要预览文本；
        - 若 output 为 "full_sop" 且 confirmed 为 True，返回完整格式化的 SOP。

        Args:
            goal: 实验目标描述文本。
            genes: 可选的基因信息字典列表。
            output: 输出类型，"advice" 返回预览，"full_sop" 返回完整方案。
            confirmed: 是否已确认生成完整 SOP，默认为 False。

        Returns:
            str: 实验预览文本或格式化后的完整 SOP 文本。
        """
        preview_genes = await self.preview(goal=goal, genes=genes)
        if output != "full_sop" or not confirmed:
            lines = ["实验设计预览：", ""]
            for gene in preview_genes:
                lines.append(f"- {gene.get('gene', '')} ({gene.get('species', '')})")
            lines.append("")
            lines.append("如需完整 CRISPR/SOP，请明确要求生成完整实验方案。")
            return "\n".join(lines)
        sops: dict[str, str] = {}
        async for event in self.run(genes=preview_genes):
            if event.get("type") == "result":
                sops = event.get("sops", {})
            elif event.get("type") == "error":
                raise RuntimeError(event.get("msg") or "实验流程失败")
        return format_sops(sops)

    def _create_pipeline(self):
        """创建实验流程管线实例。

        若初始化时提供了 pipeline_factory，则调用该工厂函数；
        否则使用默认的 ExperimentPipeline 类进行构造。

        Returns:
            ExperimentPipeline: 实验流程管线实例。
        """
        if self.pipeline_factory is not None:
            return self.pipeline_factory()
        from nutrimaster.experiment.crispr.pipeline import ExperimentPipeline

        return ExperimentPipeline()


class GeneTransferDesignService:
    """转基因实验方案生成服务，供 Web API 调用。

    协调从 LLM 推断受体物种、获取基因序列到 SOP 模板填充的完整流程。
    """

    def __init__(self, *, execution_gate: ExperimentExecutionGate | None = None):
        self.execution_gate = execution_gate or ExperimentExecutionGate()

    async def preview_species(
        self,
        *,
        goal: str,
        selected_gene_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """使用 LLM 从对话文本推断转基因受体物种，并以 NCBI 验证基因信息。

        Args:
            goal: 包含对话上下文的目标描述文本。
            selected_gene_names: 可选，用户已选定的基因名称列表（仅用于 NCBI 验证）。

        Returns:
            dict 包含：
              - "genes": 验证后的基因列表
              - "species": LLM 推断的受体物种拉丁名列表
        """
        goal = normalize_experiment_goal(goal)
        selected_gene_names = normalize_selected_gene_names(selected_gene_names)
        def _preview_sync() -> dict[str, Any]:
            # See ExperimentDesignService.preview: the worker, rather than the
            # cancellable coroutine, owns the capacity lease.
            with self.execution_gate.hold():
                pipeline = self._create_pipeline()
                try:
                    if selected_gene_names:
                        genes = pipeline.extract_selected_genes_with_llm(
                            goal,
                            selected_gene_names,
                        )
                    else:
                        genes = pipeline.extract_genes_with_llm(goal)

                    genes = normalize_experiment_genes(
                        genes,
                        require_nonempty=True,
                        require_species=True,
                    )
                    verified = verify_genes_with_ncbi(genes)
                    species = extract_transgenic_species_with_llm(goal)
                    if not isinstance(species, list):
                        raise ExperimentInputError("species 必须是数组")
                    if len(species) > MAX_RECIPIENT_SPECIES:
                        raise ExperimentInputError(
                            f"species 最多包含 {MAX_RECIPIENT_SPECIES} 项"
                        )
                    species = [
                        _normalize_bounded_text(
                            item,
                            field=f"species[{index}]",
                            max_chars=MAX_SPECIES_NAME_CHARS,
                        )
                        for index, item in enumerate(species)
                    ]
                    return {"genes": verified, "species": species}
                finally:
                    pipeline.cleanup()

        return await asyncio.to_thread(_preview_sync)

    async def run(
        self,
        *,
        genes: list[dict[str, Any]],
        species_list: list[str],
    ) -> dict[str, str]:
        """执行完整的转基因实验方案生成流程。

        Args:
            genes: 经过 NCBI 验证的基因信息字典列表。
            species_list: 受体物种拉丁名列表。

        Returns:
            dict[str, str]: 以物种名为键、Markdown SOP 文本为值的字典。
        """
        genes = normalize_experiment_genes(
            genes,
            require_nonempty=True,
            require_species=True,
        )
        assert genes is not None
        species_list = normalize_recipient_species(species_list)
        from nutrimaster.experiment.gene_transfer.gene2updown import (
            run_gene_transfer_sequences,
        )
        from nutrimaster.experiment.gene_transfer.experiment_design import (
            run_gene_transfer_design,
        )

        def _run_sync() -> dict[str, str]:
            with self.execution_gate.hold():
                gene_results = run_gene_transfer_sequences(genes)
                sops = run_gene_transfer_design(
                    gene_results,
                    species_list,
                )
                validate_sop_output(sops)
                return sops

        return await asyncio.to_thread(_run_sync)

    def _create_pipeline(self):
        from nutrimaster.experiment.crispr.pipeline import ExperimentPipeline
        return ExperimentPipeline()
