from __future__ import annotations

from typing import Any

from nutrimaster.agent.tools.base import BaseTool
from nutrimaster.experiment import ExperimentDesignService, GeneTransferDesignService
from nutrimaster.experiment.sop import format_sops


class ExperimentDesignTool(BaseTool):
    """实验设计工具，支持 CRISPR 基因敲除和过表达/转基因两种实验类型的方案生成。

    封装 ExperimentDesignService（CRISPR）和 GeneTransferDesignService（基因转化），
    通过 experiment_type 参数路由到对应的服务。两种类型均采用两阶段交互：
    先预览基因验证结果，确认后再生成完整 SOP。
    """
    name = "experiment_design"
    description = (
        "植物实验设计工具：根据实验类型生成实验建议或完整 SOP。"
        "crispr 类型执行 CRISPR 靶点设计（基因敲除/编辑）；"
        "gene_transfer 类型执行过表达/转基因实验方案设计。"
        "首次调用返回基因验证预览，用户确认后设置 confirmed=true 生成完整 SOP。"
    )

    def __init__(
        self,
        crispr_service: ExperimentDesignService,
        gene_transfer_service: GeneTransferDesignService,
    ):
        self.crispr_service = crispr_service
        self.gene_transfer_service = gene_transfer_service

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "experiment_type": {
                            "type": "string",
                            "enum": ["crispr", "gene_transfer"],
                            "description": (
                                "实验类型。crispr：CRISPR 基因敲除/编辑，需要靶点设计；"
                                "gene_transfer：过表达或转基因，需要上下游序列和 SOP。"
                            ),
                        },
                        "goal": {
                            "type": "string",
                            "description": "用户的实验目标或原始需求，包含基因名、物种等信息。",
                        },
                        "genes": {
                            "type": "array",
                            "description": "可选。已知基因列表，每项包含 gene（必填）和 species（可选）。",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "gene": {"type": "string"},
                                    "species": {"type": "string"},
                                },
                                "required": ["gene"],
                            },
                        },
                        "output": {
                            "type": "string",
                            "enum": ["advice", "full_sop"],
                            "description": "advice 返回基因验证预览和实验建议；full_sop 生成完整 SOP（需 confirmed=true）。",
                        },
                        "confirmed": {
                            "type": "boolean",
                            "description": "用户确认预览结果后设为 true，触发完整 pipeline。首次调用保持 false。",
                        },
                    },
                    "required": ["experiment_type", "goal"],
                },
            },
        }

    async def execute(
        self,
        experiment_type: str,
        goal: str,
        genes: list[dict[str, Any]] | None = None,
        output: str = "advice",
        confirmed: bool = False,
        **_,
    ) -> str:
        if experiment_type == "gene_transfer":
            return await self._run_gene_transfer(goal=goal, genes=genes, output=output, confirmed=confirmed)
        return await self.crispr_service.tool_call(
            goal=goal,
            genes=genes,
            output=output,
            confirmed=confirmed,
        )

    async def _run_gene_transfer(
        self,
        goal: str,
        genes: list[dict[str, Any]] | None = None,
        output: str = "advice",
        confirmed: bool = False,
    ) -> str:
        preview = await self.gene_transfer_service.preview_species(goal=goal)
        verified_genes: list[dict[str, Any]] = preview.get("genes", [])
        species_list: list[str] = preview.get("species", [])

        if output != "full_sop" or not confirmed:
            lines = ["转基因实验设计预览：", ""]
            lines.append("**基因（供体）：**")
            for g in verified_genes:
                ncbi = "✓ NCBI 已验证" if g.get("ncbi_found") else "未在 NCBI 找到"
                lines.append(f"- {g.get('gene', '')} ({g.get('species', '')})  {ncbi}")
            lines.append("")
            lines.append("**受体物种：**")
            for sp in species_list:
                lines.append(f"- {sp}")
            lines.append("")
            lines.append("如需生成完整过表达实验 SOP，请确认以上信息后明确要求生成完整方案。")
            return "\n".join(lines)

        sops = await self.gene_transfer_service.run(genes=verified_genes, species_list=species_list)
        return format_sops(sops)
