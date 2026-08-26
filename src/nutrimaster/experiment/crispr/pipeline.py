from __future__ import annotations

import json
import logging
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path
from nutrimaster.experiment.crispr import gene2accession as step1_gene2acc
from nutrimaster.experiment.crispr import accession2sequence as step2_acc2seq
from nutrimaster.experiment.crispr import crispr_target as step3_crispr
from nutrimaster.experiment.crispr import experiment_design as step4_design
from nutrimaster.experiment.llm import call_experiment_llm

logger = logging.getLogger(__name__)


def _json_from_llm_text(raw: str):
    """从 LLM 返回的文本中提取 JSON 数据。

    支持处理被 Markdown 代码块（```json ... ```）包裹的 JSON 文本。

    Args:
        raw: LLM 返回的原始文本，可能包含 Markdown 代码块标记。

    Returns:
        解析后的 JSON 对象（通常为 list 或 dict）。

    Raises:
        json.JSONDecodeError: JSON 解析失败时抛出。
    """
    text = raw.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


class ExperimentPipeline:
    """CRISPR 实验设计流水线编排器。

    负责协调从基因提取、accession 查询、序列下载、CRISPR 靶点设计到实验方案生成的完整流程。
    """

    def __init__(self, work_dir: Path | None = None):
        """初始化 CRISPR 实验设计流水线。

        Args:
            work_dir: 工作目录路径。如果为 None，将自动创建一个以 "crispr_pipeline_" 为前缀的临时目录。
        """
        self.work_dir = work_dir or Path(tempfile.mkdtemp(prefix="crispr_pipeline_"))

    def extract_genes_with_llm(self, answer_text: str) -> list[dict]:
        """使用 LLM 从文本中自动提取需要进行 CRISPR 实验的基因名称和物种信息。

        提取范围包括：用户要求生成 SOP 的基因、被建议编辑/敲除/过表达的基因。

        Args:
            answer_text: 包含基因信息的文本内容（通常来自 RAG 系统的回答）。

        Returns:
            list[dict]: 基因信息字典列表，每个字典包含 "gene"（基因名）和 "species"（物种拉丁名）。

        Raises:
            ValueError: 未能从文本中提取到任何基因时抛出。
        """
        prompt = (
            "从以下文本中提取所有需要进行实验操作的基因名称和对应物种（拉丁名）。\n"
            "包括：用户明确要求生成 SOP 的基因、被建议编辑/敲除/过表达的基因。\n"
            '返回 JSON 数组格式: [{"gene": "Shrunken-2", "species": "Zea mays"}]\n'
            "基因名保持原文中的写法。如果文本未明确物种，根据上下文推断。\n"
            "如果没有找到，返回空数组 []。\n\n"
            f"文本：\n{answer_text}"
        )
        genes = _json_from_llm_text(
            call_experiment_llm(
                [{"role": "user", "content": prompt}],
                temperature=0,
            ).content
        )
        if not isinstance(genes, list) or not genes:
            raise ValueError("未从回答中提取到建议编辑的基因")
        return genes

    def extract_selected_genes_with_llm(self, answer_text: str, gene_names: list[str]) -> list[dict]:
        """使用 LLM 为用户选定的基因列表匹配对应的物种信息。

        根据文本上下文和基因名前缀（如 Gm=大豆、At=拟南芥）推断每个基因的物种拉丁名。

        Args:
            answer_text: 包含上下文信息的文本内容，用于推断物种。
            gene_names: 用户选定的基因名称列表。

        Returns:
            list[dict]: 基因信息字典列表，每个字典包含 "gene"（基因名）和 "species"（物种拉丁名）。

        Raises:
            ValueError: 未能为选定基因解析物种信息时抛出。
        """
        gene_list = ", ".join(gene_names)
        prompt = (
            f"以下是用户选定的基因列表：{gene_list}\n\n"
            "请根据下方文本的上下文，为每个基因确定其对应的物种（拉丁名）。\n"
            "如果文本中未提及某个基因的物种，请根据基因名前缀推断"
            "（如 Gm=Glycine max, At=Arabidopsis thaliana, Os=Oryza sativa, "
            "Sl=Solanum lycopersicum, Zm=Zea mays）。\n"
            '返回 JSON 数组格式: [{"gene": "GmFAD2", "species": "Glycine max"}]\n\n'
            f"文本：\n{answer_text}"
        )
        genes = _json_from_llm_text(
            call_experiment_llm(
                [{"role": "user", "content": prompt}],
                temperature=0,
            ).content
        )
        if not isinstance(genes, list) or not genes:
            raise ValueError("未能为选定的基因解析物种信息")
        return genes

    def run_all_from_genes(self, genes: list[dict]) -> Generator[dict, None, None]:
        """从基因列表出发，执行完整的 CRISPR 实验设计流水线。

        流水线包含四个步骤：
        1. 查询 NCBI 获取基因的 accession 编号
        2. 从 NCBI 下载基因核酸序列
        3. 在线设计 CRISPR 靶点
        4. 生成实验方案 SOP 文档

        通过生成器逐步产出进度信息和最终结果，便于前端实时展示进度。

        Args:
            genes: 基因信息字典列表，每个字典需包含 "gene"（基因名）和 "species"（物种名）。

        Yields:
            dict: 进度或结果字典，类型字段 "type" 为 "progress"（进度更新）、"result"（最终结果）
                或 "error"（错误信息）。
        """
        try:
            gene_names = ", ".join(gene["gene"] for gene in genes)

            yield {"type": "progress", "step": 1, "total": 4, "msg": f"正在查询 NCBI 获取 accession（{gene_names}）..."}
            acc_files = step1_gene2acc.run_gene2accession(genes, self.work_dir)
            yield {"type": "progress", "step": 1, "total": 4, "msg": "Accession 查询完成", "done": True}

            yield {"type": "progress", "step": 2, "total": 4, "msg": "正在从 NCBI 下载基因序列..."}
            fasta_files = step2_acc2seq.run_accession2sequence(acc_files, self.work_dir)
            yield {"type": "progress", "step": 2, "total": 4, "msg": "序列下载完成", "done": True}

            yield {"type": "progress", "step": 3, "total": 4, "msg": "正在设计 CRISPR 靶点（可能需要 10-30 秒）..."}
            target_files = step3_crispr.run_crispr_target(fasta_files, self.work_dir)
            yield {"type": "progress", "step": 3, "total": 4, "msg": "CRISPR 靶点设计完成", "done": True}

            yield {"type": "progress", "step": 4, "total": 4, "msg": "正在生成实验方案 SOP..."}
            sops = step4_design.run_experiment_design(fasta_files, target_files, self.work_dir)
            yield {"type": "progress", "step": 4, "total": 4, "msg": "实验方案生成完成", "done": True}
            yield {"type": "result", "sops": sops}
        except MemoryError:
            # Memory exhaustion is a process-level capacity signal.  The Web
            # and service layers must see it unchanged instead of continuing
            # to allocate while serializing a normal SSE error event.
            raise
        except Exception as exc:
            logger.exception("实验方案 pipeline 执行出错")
            yield {"type": "error", "msg": str(exc)}

    def cleanup(self) -> None:
        """清理流水线的工作目录。

        递归删除工作目录及其所有内容，忽略删除过程中的错误。
        """
        shutil.rmtree(self.work_dir, ignore_errors=True)


__all__ = ["ExperimentPipeline"]
