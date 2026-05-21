from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any, AsyncGenerator

from nutrimaster.experiment.crispr import run_crispr_workflow, stream_crispr_workflow
from nutrimaster.experiment.gene_validation import extract_transgenic_species_with_llm, verify_genes_with_ncbi
from nutrimaster.experiment.sop import format_sops


class ExperimentDesignService:
    """CRISPR 实验设计服务的高层封装，供智能体和 Web API 调用。

    提供基因预览、实验运行和工具调用等异步接口，
    将基因提取、NCBI 验证和 SOP 生成等步骤整合为统一的工作流。
    """

    def __init__(self, pipeline_factory=None):
        """初始化实验设计服务。

        Args:
            pipeline_factory: 可选的管线工厂函数，调用后返回 ExperimentPipeline 实例。
                若为 None，则使用默认的 ExperimentPipeline 构造。
        """
        self.pipeline_factory = pipeline_factory

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
        pipeline = self._create_pipeline()
        try:
            if genes:
                extracted = genes
            elif selected_gene_names:
                extracted = await asyncio.to_thread(
                    pipeline.extract_selected_genes_with_llm,
                    goal,
                    selected_gene_names,
                )
            else:
                extracted = await asyncio.to_thread(pipeline.extract_genes_with_llm, goal)
            return await asyncio.to_thread(verify_genes_with_ncbi, extracted)
        finally:
            pipeline.cleanup()

    async def run(self, *, genes: list[dict[str, Any]]) -> AsyncGenerator[dict, None]:
        """流式运行完整的 CRISPR 实验设计工作流，逐步 yield progress/result/error 事件。

        在后台线程中执行同步 pipeline，通过 queue 将事件传回主线程 async generator。

        Args:
            genes: 基因信息字典列表。

        Yields:
            dict: pipeline 产生的事件（type: progress / result / error）。
        """
        pipeline = self._create_pipeline()
        q: queue.Queue = queue.Queue()
        _SENTINEL = object()

        def _run_in_thread():
            try:
                for event in stream_crispr_workflow(pipeline, genes):
                    q.put(event)
            except Exception as exc:
                q.put({"type": "error", "msg": str(exc)})
            finally:
                pipeline.cleanup()
                q.put(_SENTINEL)

        loop = asyncio.get_event_loop()
        thread = threading.Thread(target=_run_in_thread, daemon=True)
        thread.start()

        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is _SENTINEL:
                break
            yield item

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
        pipeline = self._create_pipeline()
        try:
            if selected_gene_names:
                genes = await asyncio.to_thread(
                    pipeline.extract_selected_genes_with_llm,
                    goal,
                    selected_gene_names,
                )
            else:
                genes = await asyncio.to_thread(pipeline.extract_genes_with_llm, goal)

            verified = await asyncio.to_thread(verify_genes_with_ncbi, genes)
            species = await asyncio.to_thread(extract_transgenic_species_with_llm, goal)
            return {"genes": verified, "species": species}
        finally:
            pipeline.cleanup()

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
        from nutrimaster.experiment.gene_transfer.gene2updown import run_gene_transfer_sequences
        from nutrimaster.experiment.gene_transfer.experiment_design import run_gene_transfer_design

        gene_results = await asyncio.to_thread(
            run_gene_transfer_sequences,
            genes,
        )
        sops = await asyncio.to_thread(
            run_gene_transfer_design,
            gene_results,
            species_list,
        )
        return sops

    def _create_pipeline(self):
        from nutrimaster.experiment.crispr.pipeline import ExperimentPipeline
        return ExperimentPipeline()
