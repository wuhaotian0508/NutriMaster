from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pickle
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("indexer")

CHUNKER_VERSION = "v2.2026-04"
CHUNK_MIN_BODY_LEN = 80
CHUNK_MAX_BODY_LEN = 1500

PLANT_FIELD_GROUPS = {
    "identity": [
        "Gene_Name",
        "Gene_Accession_Number",
        "Chromosome_Position",
        "Species",
        "Species_Latin_Name",
        "Variety",
        "Reference_Genome_Version",
        "QTL_or_Locus_Name",
    ],
    "overview": [
        "Summary_key_Findings_of_Core_Gene",
        "Summary_Key_Findings_of_Core_Gene",
        "Core_Phenotypic_Effect",
        "Research_Topic",
        "Trait_Category",
    ],
    "mechanism": [
        "Regulatory_Mechanism",
        "Regulatory_Pathway",
        "Biosynthetic_Pathway",
        "Upstream_or_Downstream_Regulation",
        "Protein_Family_or_Domain",
        "Subcellular_Localization",
        "Interacting_Proteins",
        "Expression_Pattern",
    ],
    "phenotype": [
        "Quantitative_Phenotypic_Alterations",
        "Other_Phenotypic_Effects",
        "Key_Environment_or_Treatment_Factor",
    ],
    "variant_breeding": [
        "Key_Variant_Site",
        "Key_Variant_Type",
        "Favorable_Allele",
        "Superior_Haplotype",
        "Breeding_Application_Value",
        "Field_Trials",
        "Genetic_Population",
        "Genomic_Selection_Application",
    ],
    "methods": [
        "Experimental_Methods",
        "Experimental_Materials",
        "Core_Validation_Method",
        "Gene_Discovery_or_Cloning_Method",
    ],
}

PATHWAY_FIELD_GROUPS = {
    "identity": [
        "Gene_Name",
        "Enzyme_Name",
        "EC_Number",
        "Protein_Family_or_Domain",
        "Gene_Accession_Number",
    ],
    "reaction": [
        "Primary_Substrate",
        "Primary_Product",
        "Catalyzed_Reaction_Description",
        "Biosynthetic_Pathway",
        "Pathway_Branch_or_Subpathway",
        "Metabolic_Step_Position",
        "End_Product_Connection_Type",
        "Rate_Limiting_or_Key_Control_Step",
    ],
    "terminal": [
        "Terminal_Metabolite",
        "Terminal_Metabolite_Class",
        "Terminal_Metabolite_Function",
        "Terminal_Metabolite_Accumulation_Site",
    ],
    "function": [
        "Core_Phenotypic_Effect",
        "Summary_Key_Findings_of_Core_Gene",
        "Core_Validation_Method",
        "Environment_or_Treatment_Factor",
    ],
    "interactions": ["Interacting_Proteins"],
    "engineering": [
        "Breeding_or_Engineering_Application_Value",
        "Potential_Tradeoffs",
        "Other_Important_Info",
    ],
}

REGULATION_CORE_FIELDS = [
    "Regulator_Type",
    "Regulation_Mode",
    "Primary_Regulatory_Targets",
    "Regulatory_Effect_on_Target_Genes",
    "Upstream_Signals_or_Inputs",
    "Metabolic_Process_Controlled",
    "Decisive_Influence_on_Target_Product",
    "Feedback_or_Feedforward_Regulation",
    "Protein_Complex_Involvement",
    "Epigenetic_Regulation",
]


@dataclass
class GeneChunk:
    """基因信息块，表示从论文中提取的一段结构化基因信息。

    Attributes:
        gene_name: 基因名称。
        paper_title: 来源论文标题。
        journal: 发表期刊。
        doi: 论文 DOI。
        gene_type: 基因分类（如 Plant_Genes、Pathway_Genes 等）。
        content: 格式化后的文本内容，用于嵌入和检索。
        metadata: 原始基因字段数据的完整字典。
        chunk_type: 信息块类型（如 gene、pathway_overview 等）。
        chunker_version: 分块器版本号。
        source_fields: 生成此块所使用的字段名称列表。
        relations: 基因间关系信息（如上下游调控、底物产物等）。
    """
    gene_name: str
    paper_title: str
    journal: str
    doi: str
    gene_type: str
    content: str
    metadata: dict[str, Any]
    chunk_type: str = "gene"
    chunker_version: str = CHUNKER_VERSION
    source_fields: list[str] = field(default_factory=list)
    relations: dict[str, Any] = field(default_factory=dict)


class FieldFormatter:
    """字段格式化器：负责将基因数据字段渲染为人类可读的文本。

    支持字段名中英文翻译、空值过滤和分组渲染。

    Attributes:
        translation: 字段名到中文翻译的映射字典。
    """

    def __init__(self, translation: dict[str, str] | None = None):
        """初始化字段格式化器。

        Args:
            translation: 字段名到中文翻译的映射字典。若为 None 则使用空字典。
        """
        self.translation = translation or {}

    @classmethod
    def from_default_translation(cls, project_root: Path | None = None) -> "FieldFormatter":
        """从默认翻译文件创建格式化器实例。

        尝试从 resources/translate.json 文件加载字段名翻译映射。
        若文件不存在则返回无翻译的默认实例。

        Args:
            project_root: 项目根目录（当前未使用）。

        Returns:
            配置好翻译映射的 FieldFormatter 实例。
        """
        path = Path(__file__).resolve().parent / "resources" / "translate.json"
        if path.exists():
            return cls(json.loads(path.read_text(encoding="utf-8")))
        return cls()

    def render_group(
        self,
        gene: dict[str, Any],
        fields: list[str],
        section_title: str | None = None,
    ) -> list[str]:
        """将一组字段渲染为格式化文本行。

        遍历指定字段列表，过滤空值和重复标签，将非空字段格式化为
        "标签: 值" 的文本行。若有内容且指定了 section_title，
        则在开头添加分节标题。

        Args:
            gene: 基因数据字典。
            fields: 要渲染的字段名称列表。
            section_title: 可选的分节标题。

        Returns:
            格式化后的文本行列表；若所有字段均为空则返回空列表。
        """
        lines: list[str] = []
        seen_keys = set()
        for field_name in fields:
            value = gene.get(field_name)
            if self.is_empty(value):
                continue
            label = self.translation.get(field_name, field_name)
            if label in seen_keys:
                continue
            seen_keys.add(label)
            if isinstance(value, list):
                value_text = "; ".join(str(item).strip() for item in value if not self.is_empty(item))
            else:
                value_text = str(value).strip()
            if value_text:
                lines.append(f"{label}: {value_text}")
        if lines and section_title:
            return [f"── {section_title} ──", *lines]
        return lines

    @staticmethod
    def is_empty(value: Any) -> bool:
        """判断字段值是否为空或无效。

        将 None、空字符串、"NA"、"null"、"Unknown" 等常见占位值
        以及空列表视为空值。

        Args:
            value: 待检查的字段值。

        Returns:
            True 表示值为空或无效，False 表示有有效内容。
        """
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() in {
                "",
                "NA",
                "na",
                "N/A",
                "null",
                "None",
                "Not established",
                "not established",
                "Unknown",
                "unknown",
            }
        return isinstance(value, list) and not value

    @staticmethod
    def header(
        paper: dict[str, Any],
        gene: dict[str, Any] | None = None,
        extras: dict[str, str] | None = None,
    ) -> list[str]:
        """生成信息块的头部元信息行。

        包含基因名称、酶名、蛋白质家族、论文标题、期刊、DOI 和物种等信息。

        Args:
            paper: 论文数据字典（包含 Title、Journal、DOI 等）。
            gene: 可选的基因数据字典。
            extras: 可选的额外键值对，将作为 [Key] Value 格式添加。

        Returns:
            头部信息的文本行列表。
        """
        lines: list[str] = []
        if gene and gene.get("Gene_Name"):
            gene_header = f"[Gene] {gene['Gene_Name']}"
            if gene.get("Enzyme_Name"):
                gene_header += f"  [Enzyme] {gene['Enzyme_Name']}"
            if gene.get("Protein_Family_or_Domain"):
                gene_header += f"  [Family] {gene['Protein_Family_or_Domain']}"
            lines.append(gene_header)
        lines.append(f"[Paper] {paper.get('Title') or ''}")
        journal_line = f"[Journal] {paper.get('Journal') or ''}"
        doi = paper.get("DOI") or ""
        if doi and doi not in {"NA", "Unknown"}:
            journal_line += f"  [DOI] {doi}"
        lines.append(journal_line)
        if gene:
            species = (
                gene.get("Species_Latin_Name")
                or gene.get("Source_Species_Latin_Name")
                or gene.get("Species")
                or gene.get("Source_Species")
            )
            if species and not FieldFormatter.is_empty(species):
                lines.append(f"[Species] {species}")
        if extras:
            for key, value in extras.items():
                if not FieldFormatter.is_empty(value):
                    lines.append(f"[{key}] {value}")
        return lines


class BaseChunker:
    """分块器基类，提供创建、后处理和拆分基因信息块的通用方法。

    Attributes:
        fmt: 字段格式化器实例。
    """

    def __init__(self, formatter: FieldFormatter):
        """初始化分块器。

        Args:
            formatter: 字段格式化器实例。
        """
        self.fmt = formatter

    def _make_chunk(
        self,
        *,
        paper: dict[str, Any],
        gene: dict[str, Any] | None,
        gene_type: str,
        chunk_type: str,
        content_lines: list[str],
        source_fields: list[str],
        relations: dict[str, Any] | None = None,
    ) -> GeneChunk | None:
        """创建单个基因信息块。

        将内容行合并为文本，检查正文长度是否达到最小阈值。
        跳过标题行（以 [ 或 ── 开头的行）计算正文长度。

        Args:
            paper: 论文数据字典。
            gene: 基因数据字典（可为 None，用于论文级别块）。
            gene_type: 基因类型分类。
            chunk_type: 信息块类型标识。
            content_lines: 内容文本行列表。
            source_fields: 生成此块使用的字段列表。
            relations: 可选的关系信息字典。

        Returns:
            GeneChunk 实例；若内容过短则返回 None。
        """
        content = "\n".join(line for line in content_lines if line is not None).strip()
        if not content:
            return None
        body_len = sum(
            len(line)
            for line in content.splitlines()
            if line and not line.startswith("[") and not line.startswith("── ")
        )
        if body_len < CHUNK_MIN_BODY_LEN:
            return None
        return GeneChunk(
            gene_name=(gene or {}).get("Gene_Name") or "__PAPER__",
            paper_title=paper.get("Title") or "Unknown",
            journal=paper.get("Journal") or "Unknown",
            doi=paper.get("DOI") or "Unknown",
            gene_type=gene_type,
            content=content,
            metadata=dict(gene or {}),
            chunk_type=chunk_type,
            chunker_version=CHUNKER_VERSION,
            source_fields=list(source_fields),
            relations=dict(relations or {}),
        )

    def _postprocess(self, chunks: list[GeneChunk | None]) -> list[GeneChunk]:
        """后处理信息块列表：过滤 None 并执行可能的拆分。

        Args:
            chunks: 可能包含 None 的信息块列表。

        Returns:
            过滤和处理后的有效信息块列表。
        """
        output: list[GeneChunk] = [] #:表示建议
        for chunk in chunks:
            if chunk is None:
                continue
            output.extend(self._maybe_split(chunk))
        return output

    def _maybe_split(self, chunk: GeneChunk) -> list[GeneChunk]:
        """检查并拆分过长的信息块。

        当前实现不进行实际拆分，直接返回原块。预留用于后续支持
        超过 CHUNK_MAX_BODY_LEN 的块的拆分逻辑。

        Args:
            chunk: 待检查的信息块。

        Returns:
            包含原始块的单元素列表。
        """
        if len(chunk.content) <= CHUNK_MAX_BODY_LEN:
            return [chunk]
        return [chunk]

    @staticmethod
    def _parse_list_field(value: Any) -> list[str]:
        """解析列表类型的字段值。

        支持输入为列表或分号分隔的字符串，过滤空值后返回字符串列表。

        Args:
            value: 字段值，可以是列表、分号分隔的字符串或空值。

        Returns:
            解析后的非空字符串列表。
        """
        if FieldFormatter.is_empty(value):
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if not FieldFormatter.is_empty(item)]
        return [item.strip() for item in str(value).split(";") if item.strip()]


class GenericChunker(BaseChunker):
    """通用分块器：适用于所有类型基因的简单分块策略。

    将每个基因的所有字段平铺为文本，生成一个信息块。
    """

    def chunk(self, paper: dict[str, Any]) -> list[GeneChunk]:
        """对论文中的所有基因进行分块。

        遍历论文中所有基因桶（Pathway_Genes、Regulation_Genes、
        Common_Genes、Plant_Genes），为每个基因生成一个信息块。

        Args:
            paper: 论文数据字典。

        Returns:
            处理后的基因信息块列表。
        """
        chunks = []
        for bucket in ("Pathway_Genes", "Regulation_Genes", "Common_Genes", "Plant_Genes"):
            for gene in paper.get(bucket) or []:
                chunks.append(self._one(paper, gene, bucket))
        return self._postprocess(chunks)

    def _one(self, paper: dict[str, Any], gene: dict[str, Any], bucket: str) -> GeneChunk | None:
        """为单个基因创建通用信息块。

        将基因名称、论文信息和所有非空字段平铺为文本内容。

        Args:
            paper: 论文数据字典。
            gene: 基因数据字典。
            bucket: 基因所属的桶名称（如 Plant_Genes）。

        Returns:
            GeneChunk 实例；若基因数据不足则可能为 None（但通用模式通常不为 None）。
        """
        gene_name = gene.get("Gene_Name") or "Unknown"
        lines = [
            f"基因名称: {gene_name}",
            f"文章: {paper.get('Title') or 'Unknown'}",
            f"期刊: {paper.get('Journal') or 'Unknown'}",
            f"DOI: {paper.get('DOI') or 'Unknown'}",
            f"基因类型: {bucket}",
            "",
        ]
        source_fields = ["Gene_Name"]
        for key, value in gene.items():
            if key == "Gene_Name" or self.fmt.is_empty(value):
                continue
            if isinstance(value, list):
                value_text = "; ".join(str(item) for item in value if not self.fmt.is_empty(item))
            else:
                value_text = str(value).strip()
            if value_text:
                lines.append(f"{self.fmt.translation.get(key, key)}: {value_text}")
                source_fields.append(key)
        return GeneChunk(
            gene_name=gene_name,
            paper_title=paper.get("Title") or "Unknown",
            journal=paper.get("Journal") or "Unknown",
            doi=paper.get("DOI") or "Unknown",
            gene_type=bucket,
            content="\n".join(lines),
            metadata=dict(gene),
            chunk_type="gene",
            chunker_version=CHUNKER_VERSION,
            source_fields=source_fields,
            relations={},
        )


class PlantGenesChunker(BaseChunker):
    """植物基因分块器：针对 Plant_Genes 按功能分组生成多个信息块。

    每个植物基因可生成多个信息块：概览、机制、表型、变异育种、方法等，
    便于更精细的检索匹配。
    """

    def chunk(self, paper: dict[str, Any]) -> list[GeneChunk]:
        """对论文中的植物基因进行多维度分块。

        为每个 Plant_Genes 基因生成概览块，以及根据信息丰富度可选生成
        机制、表型、变异育种和方法等专题块。

        Args:
            paper: 论文数据字典。

        Returns:
            处理后的基因信息块列表。
        """
        chunks: list[GeneChunk | None] = []
        for gene in paper.get("Plant_Genes") or []:
            header = self.fmt.header(paper, gene)
            identity = PLANT_FIELD_GROUPS["identity"]
            overview = self.fmt.render_group(gene, PLANT_FIELD_GROUPS["overview"], "Overview")
            identity_lines = self.fmt.render_group(gene, identity, "Identity")
            chunks.append(
                self._make_chunk(
                    paper=paper,
                    gene=gene,
                    gene_type="Plant_Genes",
                    chunk_type="plant_gene_overview",
                    content_lines=header + [""] + identity_lines + [""] + overview,
                    source_fields=identity + PLANT_FIELD_GROUPS["overview"],
                )
            )
            for group_key, chunk_type, min_nonempty in (
                ("mechanism", "plant_gene_mechanism", 2),
                ("phenotype", "plant_gene_phenotype", 1),
                ("variant_breeding", "plant_gene_variant_breeding", 1),
                ("methods", "plant_gene_methods", 1),
            ):
                fields = PLANT_FIELD_GROUPS[group_key]
                if sum(1 for field_name in fields if not self.fmt.is_empty(gene.get(field_name))) < min_nonempty:
                    continue
                body = self.fmt.render_group(gene, fields, group_key.title())
                chunks.append(
                    self._make_chunk(
                        paper=paper,
                        gene=gene,
                        gene_type="Plant_Genes",
                        chunk_type=chunk_type,
                        content_lines=header + [""] + body,
                        source_fields=fields,
                    )
                )
        return self._postprocess(chunks)


class PathwayChainChunker(BaseChunker):
    """代谢通路链分块器：适用于线性代谢通路基因。

    除了为每个基因生成单独的信息块外，还生成通路概览块和
    反应链图谱块，展示底物-产物的转化链路。
    """

    def chunk(self, paper: dict[str, Any]) -> list[GeneChunk]:
        """对通路基因进行分块，包含通路级别的概览信息。

        生成通路概览块、反应链图谱块，以及每个基因的详细信息块。

        Args:
            paper: 论文数据字典。

        Returns:
            处理后的基因信息块列表。
        """
        genes = paper.get("Pathway_Genes") or []
        chunks: list[GeneChunk | None] = [self._pathway_overview(paper, genes), self._pathway_graph(paper, genes)]
        chunks.extend(self._pathway_gene(paper, gene) for gene in genes)
        return self._postprocess(chunks)

    def _pathway_overview(self, paper: dict[str, Any], genes: list[dict[str, Any]]) -> GeneChunk | None:
        """生成通路概览信息块。

        汇总通路名称、终产物和涉及的基因数量。

        Args:
            paper: 论文数据字典。
            genes: 通路基因列表。

        Returns:
            通路概览信息块；若内容不足则返回 None。
        """
        names = [gene.get("Gene_Name") or "?" for gene in genes]
        pathways = {gene.get("Biosynthetic_Pathway") for gene in genes if not self.fmt.is_empty(gene.get("Biosynthetic_Pathway"))}
        terminals = {gene.get("Terminal_Metabolite") for gene in genes if not self.fmt.is_empty(gene.get("Terminal_Metabolite"))}
        body = [
            "── Pathway Overview ──",
            f"通路: {'; '.join(sorted(pathways)) or 'NA'}",
            f"终产物: {'; '.join(sorted(terminals)) or 'NA'}",
            f"涉及基因 ({len(genes)}): {', '.join(names)}",
        ]
        return self._make_chunk(
            paper=paper,
            gene=None,
            gene_type="__PAPER__",
            chunk_type="pathway_overview",
            content_lines=self.fmt.header(paper) + [""] + body,
            source_fields=["Biosynthetic_Pathway", "Terminal_Metabolite", "Gene_Name"],
            relations={"pathways": list(pathways), "terminals": list(terminals), "genes": names},
        )

    def _pathway_graph(self, paper: dict[str, Any], genes: list[dict[str, Any]]) -> GeneChunk | None:
        """生成通路反应链图谱信息块。

        以 "[步骤] 底物 --(基因)--> 产物" 格式展示代谢反应链。

        Args:
            paper: 论文数据字典。
            genes: 通路基因列表。

        Returns:
            反应链图谱信息块；若内容不足则返回 None。
        """
        steps = [
            f"[{gene.get('Metabolic_Step_Position') or ''}] {gene.get('Primary_Substrate') or '?'} --({gene.get('Gene_Name') or '?'})--> {gene.get('Primary_Product') or '?'}"
            for gene in genes
        ]
        return self._make_chunk(
            paper=paper,
            gene=None,
            gene_type="__PAPER__",
            chunk_type="pathway_graph",
            content_lines=self.fmt.header(paper) + ["", "── Reaction Chain ──", *steps],
            source_fields=["Primary_Substrate", "Primary_Product", "Metabolic_Step_Position", "Gene_Name"],
        )

    def _pathway_gene(self, paper: dict[str, Any], gene: dict[str, Any]) -> GeneChunk | None:
        """为单个通路基因创建信息块。

        Args:
            paper: 论文数据字典。
            gene: 基因数据字典。

        Returns:
            通路基因信息块；若内容不足则返回 None。
        """
        return _pathway_gene_chunk(self, paper, gene, "Pathway_Genes", "pathway_gene")


class MixedBucketChunker(BaseChunker):
    """混合桶分块器：适用于同时包含通路基因和调控基因的论文。

    生成论文概览、调控网络图谱，以及各类基因的详细信息块。
    """

    def chunk(self, paper: dict[str, Any]) -> list[GeneChunk]:
        """对包含多种基因类型的论文进行分块。

        分别处理 Common_Genes、Pathway_Genes 和 Regulation_Genes，
        并生成论文级别的概览和调控网络块。

        Args:
            paper: 论文数据字典。

        Returns:
            处理后的基因信息块列表。
        """
        common = paper.get("Common_Genes") or []
        pathway = paper.get("Pathway_Genes") or []
        regulation = paper.get("Regulation_Genes") or []
        chunks: list[GeneChunk | None] = [
            self._paper_overview(paper, common, pathway, regulation),
            self._regulatory_network(paper, regulation),
        ]
        chunks.extend(_pathway_gene_chunk(self, paper, gene, "Common_Genes", "common_gene") for gene in common)
        chunks.extend(_pathway_gene_chunk(self, paper, gene, "Pathway_Genes", "pathway_gene") for gene in pathway)
        chunks.extend(self._regulation_gene(paper, gene) for gene in regulation)
        return self._postprocess(chunks)

    def _paper_overview(
        self,
        paper: dict[str, Any],
        common: list[dict[str, Any]],
        pathway: list[dict[str, Any]],
        regulation: list[dict[str, Any]],
    ) -> GeneChunk | None:
        """生成论文级别的基因目录概览块。

        列出论文中各类基因的名称和数量，提供全局视图。

        Args:
            paper: 论文数据字典。
            common: Common_Genes 基因列表。
            pathway: Pathway_Genes 基因列表。
            regulation: Regulation_Genes 基因列表。

        Returns:
            论文概览信息块；若内容不足则返回 None。
        """
        body = ["── Gene Directory ──"]
        for label, genes in (
            ("Common_Genes", common),
            ("Pathway_Genes", pathway),
            ("Regulation_Genes", regulation),
        ):
            if genes:
                names = []
                for gene in genes:
                    name = gene.get("Gene_Name") or "?"
                    enzyme = gene.get("Enzyme_Name") or ""
                    names.append(f"{name}" + (f" ({enzyme})" if enzyme else ""))
                body.append(f"{label} ({len(genes)}): {', '.join(names)}")
        return self._make_chunk(
            paper=paper,
            gene=None,
            gene_type="__PAPER__",
            chunk_type="paper_overview",
            content_lines=self.fmt.header(paper) + [""] + body,
            source_fields=["Gene_Name"],
        )

    def _regulatory_network(self, paper: dict[str, Any], regulation: list[dict[str, Any]]) -> GeneChunk | None:
        """生成调控网络信息块。

        提取调控基因及其靶基因的调控关系，构建 "调控因子 -> 靶基因" 的网络视图。

        Args:
            paper: 论文数据字典。
            regulation: Regulation_Genes 基因列表。

        Returns:
            调控网络信息块；若无调控关系则返回 None。
        """
        body = ["── Regulatory Network ──"]
        regulates: dict[str, list[str]] = {}
        regulated_by: dict[str, list[str]] = {}
        for gene in regulation:
            regulator = gene.get("Gene_Name") or "?"
            targets = self._parse_list_field(gene.get("Primary_Regulatory_Targets"))
            if not targets:
                continue
            regulates[regulator] = targets
            for target in targets:
                regulated_by.setdefault(target, []).append(regulator)
            effect = gene.get("Regulatory_Effect_on_Target_Genes") or ""
            body.append(f"  {regulator} → {', '.join(targets)}" + (f" [{effect}]" if effect else ""))
        if len(body) == 1:
            return None
        return self._make_chunk(
            paper=paper,
            gene=None,
            gene_type="__PAPER__",
            chunk_type="regulatory_network",
            content_lines=self.fmt.header(paper) + [""] + body,
            source_fields=["Gene_Name", "Primary_Regulatory_Targets", "Regulatory_Effect_on_Target_Genes"],
            relations={"regulates": regulates, "regulated_by": regulated_by},
        )

    def _regulation_gene(self, paper: dict[str, Any], gene: dict[str, Any]) -> GeneChunk | None:
        """为单个调控基因创建信息块。

        按调控核心、终产物、功能、互作蛋白等分节渲染基因信息。

        Args:
            paper: 论文数据字典。
            gene: 调控基因数据字典。

        Returns:
            调控基因信息块；若内容不足则返回 None。
        """
        header = self.fmt.header(paper, gene)
        sections = [
            self.fmt.render_group(gene, REGULATION_CORE_FIELDS, "Regulation Core"),
            self.fmt.render_group(gene, PATHWAY_FIELD_GROUPS["terminal"], "Terminal"),
            self.fmt.render_group(gene, PATHWAY_FIELD_GROUPS["function"], "Function"),
            self.fmt.render_group(gene, PATHWAY_FIELD_GROUPS["interactions"], "Interactions"),
        ]
        content = header + [""]
        for section in sections:
            if section:
                content.extend(section + [""])
        return self._make_chunk(
            paper=paper,
            gene=gene,
            gene_type="Regulation_Genes",
            chunk_type="regulation_gene",
            content_lines=content,
            source_fields=REGULATION_CORE_FIELDS + PATHWAY_FIELD_GROUPS["terminal"] + PATHWAY_FIELD_GROUPS["function"],
            relations={
                "regulates": self._parse_list_field(gene.get("Primary_Regulatory_Targets")),
                "upstream": self._parse_list_field(gene.get("Upstream_Signals_or_Inputs")),
                "interacts_with": self._parse_list_field(gene.get("Interacting_Proteins")),
            },
        )


def _pathway_gene_chunk(
    chunker: BaseChunker,
    paper: dict[str, Any],
    gene: dict[str, Any],
    gene_type: str,
    chunk_type: str,
) -> GeneChunk | None:
    """为通路类型基因创建信息块（通用辅助函数）。

    按反应、功能、互作和工程应用等分节渲染基因信息，
    被 PathwayChainChunker 和 MixedBucketChunker 共用。

    Args:
        chunker: 分块器实例（用于访问格式化器和 _make_chunk）。
        paper: 论文数据字典。
        gene: 基因数据字典。
        gene_type: 基因类型分类。
        chunk_type: 信息块类型标识。

    Returns:
        通路基因信息块；若无有效分节内容则返回 None。
    """
    header = chunker.fmt.header(paper, gene)
    sections = []
    source_fields: list[str] = []
    for group in ("reaction", "function", "interactions", "engineering"):
        fields = PATHWAY_FIELD_GROUPS[group]
        section = chunker.fmt.render_group(gene, fields, group.title())
        if section:
            sections.append(section)
            source_fields.extend(fields)
    if not sections:
        return None
    content = header + [""]
    for section in sections:
        content.extend(section)
    return chunker._make_chunk(
        paper=paper,
        gene=gene,
        gene_type=gene_type,
        chunk_type=chunk_type,
        content_lines=content,
        source_fields=source_fields,
        relations={
            "substrate": gene.get("Primary_Substrate"),
            "product": gene.get("Primary_Product"),
            "pathway": gene.get("Biosynthetic_Pathway"),
        },
    )


def _looks_like_linear_pathway(genes: list[dict[str, Any]]) -> bool:
    """判断基因集合是否像一条线性代谢通路。

    如果终产物种类不超过 2 种且通路名称不超过 2 个，则认为是线性通路。

    Args:
        genes: 基因数据字典列表。

    Returns:
        True 表示可能是线性通路，False 表示不是。
    """
    terminals = {
        gene.get("Terminal_Metabolite")
        for gene in genes
        if not FieldFormatter.is_empty(gene.get("Terminal_Metabolite"))
    }
    pathways = {
        gene.get("Biosynthetic_Pathway")
        for gene in genes
        if not FieldFormatter.is_empty(gene.get("Biosynthetic_Pathway"))
    }
    return len(terminals) <= 2 and len(pathways) <= 2


def route_paper(paper: dict[str, Any]) -> str:
    """根据论文包含的基因类型选择分块策略路由。

    决策逻辑：
    - 有 Plant_Genes -> "plant_genes"
    - 有 Pathway + Regulation 或 Common + Regulation -> "mixed_bucket"
    - 仅有 Pathway 且 >=3 个基因且为线性通路 -> "pathway_chain"
    - 其他情况 -> "generic"

    Args:
        paper: 论文数据字典。

    Returns:
        路由名称字符串，对应不同的分块器类。
    """
    has_plant = bool(paper.get("Plant_Genes"))
    has_common = bool(paper.get("Common_Genes"))
    has_pathway = bool(paper.get("Pathway_Genes"))
    has_regulation = bool(paper.get("Regulation_Genes"))
    if has_plant:
        return "plant_genes"
    if (has_pathway and has_regulation) or (has_common and has_regulation):
        return "mixed_bucket"
    if has_pathway and not has_regulation:
        genes = paper.get("Pathway_Genes") or []
        if len(genes) >= 3 and _looks_like_linear_pathway(genes):
            return "pathway_chain"
    return "generic"


def chunk_paper(paper: dict[str, Any]) -> list[GeneChunk]:
    """对整篇论文进行分块处理的入口函数。

    根据论文包含的基因类型自动选择合适的分块策略（通用、植物基因、
    通路链、混合桶），若选定策略失败则回退到通用策略。

    Args:
        paper: 论文数据字典，包含 Title、Journal、DOI 和各类基因列表。

    Returns:
        论文中所有基因的信息块列表。
    """
    formatter = FieldFormatter.from_default_translation()
    chunkers = {
        "generic": GenericChunker(formatter),
        "plant_genes": PlantGenesChunker(formatter),
        "pathway_chain": PathwayChainChunker(formatter),
        "mixed_bucket": MixedBucketChunker(formatter),
    }
    route = route_paper(paper)
    try:
        chunks = chunkers[route].chunk(paper)
    except Exception:
        route = "generic"
        chunks = chunkers["generic"].chunk(paper)
    for chunk in chunks:
        chunk.relations.setdefault("__router__", route)
    return chunks


def sha256_of(path: Path, buf_size: int = 1 << 20) -> str:
    """计算文件的 SHA-256 哈希值。

    分块读取文件内容并计算哈希，适用于大文件。

    Args:
        path: 文件路径。
        buf_size: 每次读取的缓冲区大小（字节），默认 1MB。

    Returns:
        文件的 SHA-256 十六进制哈希字符串。
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        while chunk := file.read(buf_size):
            digest.update(chunk)
    return digest.hexdigest()


class IncrementalIndexer:
    """增量索引构建器：基于文件哈希和分块器版本实现增量更新。

    只重新处理变更的文件，复用未变更文件的已有索引数据，
    大幅减少重建索引所需的时间和 API 调用。

    Attributes:
        index_dir: 索引存储目录。
        data_dir: 数据文件目录。
        embed_fn: 文本嵌入函数。
        file_pattern: 数据文件匹配模式。
    """

    def __init__(
        self,
        index_dir: Path,
        data_dir: Path,
        embed_fn: Callable[[list[str]], np.ndarray],
        file_pattern: str = "*.json",
    ):
        """初始化增量索引构建器。

        Args:
            index_dir: 索引文件存储目录（自动创建）。
            data_dir: 包含待索引 JSON 文件的数据目录。
            embed_fn: 文本嵌入函数，接受字符串列表，返回 numpy 数组。
            file_pattern: 数据文件的 glob 匹配模式，默认 "*.json"。
        """
        self.index_dir = Path(index_dir)
        self.data_dir = Path(data_dir)
        self.embed_fn = embed_fn
        self.file_pattern = file_pattern
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_path = self.index_dir / "chunks.pkl"
        self.embeds_path = self.index_dir / "embeddings.npy"
        self.manifest_path = self.index_dir / "manifest.json"

    def monitor_on_startup(self):
        """启动时记录数据目录监控信息。

        记录数据目录路径和其中已验证的 JSON 文件数量。
        """
        logger.info(
            "[monitor] data_dir=%s verified JSON=%d",
            self.data_dir,
            len(list(self.data_dir.glob(self.file_pattern))),
        )

    def build_incremental(self, *, force: bool = False, batch_embed_size: int = 32, load_paper_fn=None):
        """执行增量索引构建。

        比较当前文件的 SHA-256 哈希和分块器版本与清单记录，
        仅对变更或新增的文件重新分块和计算嵌入。

        Args:
            force: 若为 True 则强制重建全部索引，忽略缓存。
            batch_embed_size: 嵌入计算的批次大小（当前未使用）。
            load_paper_fn: 自定义论文加载函数，接受 Path 返回 GeneChunk 列表。

        Returns:
            二元组 (chunks, embeddings)：完整的信息块列表和对应的嵌入矩阵。
        """
        load_paper_fn = load_paper_fn or self._load_paper
        files = sorted(self.data_dir.glob(self.file_pattern))
        manifest = {} if force else self._load_manifest()
        old_chunks, old_embeddings = ([], None) if force else self._load_existing()
        if not self._manifest_reusable(manifest, old_chunks, old_embeddings):
            manifest = {}
            old_chunks, old_embeddings = [], None

        file_shas = {path.name: sha256_of(path) for path in files}
        to_keep = []
        to_rebuild = []
        for path in files:
            entry = manifest.get(path.name)
            if (
                entry
                and entry.get("sha") == file_shas[path.name]
                and entry.get("chunker_version") == CHUNKER_VERSION
            ):
                to_keep.append(path.name)
            else:
                to_rebuild.append(path)

        final_chunks: list[GeneChunk] = []
        final_embedding_parts = []
        new_manifest = {}
        cursor = 0

        for name in to_keep:
            entry = manifest[name]
            start = int(entry["start"])
            end = int(entry["end"])
            chunks = old_chunks[start:end]
            final_chunks.extend(chunks)
            if old_embeddings is not None:
                final_embedding_parts.append(old_embeddings[start:end])
            new_manifest[name] = {
                "sha": entry["sha"],
                "chunker_version": CHUNKER_VERSION,
                "n_chunks": len(chunks),
                "start": cursor,
                "end": cursor + len(chunks),
            }
            cursor += len(chunks)

        for i, path in enumerate(to_rebuild, 1):
            logger.info(f"Processing {i}/{len(to_rebuild)}: {path.name}")
            chunks = load_paper_fn(path) or []
            embeddings = self.embed_fn([chunk.content for chunk in chunks]) if chunks else None
            if embeddings is not None and embeddings.shape[0] != len(chunks):
                raise RuntimeError("embedding count does not match chunk count")
            start = cursor
            final_chunks.extend(chunks)
            cursor += len(chunks)
            if embeddings is not None:
                final_embedding_parts.append(embeddings)
            new_manifest[path.name] = {
                "sha": file_shas[path.name],
                "chunker_version": CHUNKER_VERSION,
                "n_chunks": len(chunks),
                "start": start,
                "end": cursor,
            }
            if i % 10 == 0 or i == len(to_rebuild):
                logger.info(f"Progress: {i}/{len(to_rebuild)} files processed, {cursor} total chunks")

        final_embeddings = None
        if final_embedding_parts:
            final_embeddings = (
                np.concatenate(final_embedding_parts, axis=0)
                if len(final_embedding_parts) > 1
                else final_embedding_parts[0]
            )
        if final_chunks and final_embeddings is None:
            raise RuntimeError("chunks exist but embeddings are missing")
        if final_embeddings is not None and final_embeddings.shape[0] != len(final_chunks):
            raise RuntimeError("final embeddings do not match chunks")

        self._save(final_chunks, final_embeddings, new_manifest)
        return final_chunks, final_embeddings

    def _load_paper(self, path: Path) -> list[GeneChunk]:
        """加载论文 JSON 文件并分块。

        Args:
            path: 论文 JSON 文件路径。

        Returns:
            该论文的基因信息块列表。
        """
        return chunk_paper(json.loads(Path(path).read_text(encoding="utf-8")))

    def _load_manifest(self) -> dict:
        """加载索引清单文件。

        读取 manifest.json，检查分块器版本是否匹配。
        若版本不匹配或文件不存在/损坏，返回空字典。

        Returns:
            清单中的文件记录字典；若不可用则返回空字典。
        """
        if not self.manifest_path.exists():
            return {}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if data.get("chunker_version") != CHUNKER_VERSION:
                return {}
            return data.get("files", {})
        except Exception:
            return {}

    def _load_existing(self):
        """加载已有的索引数据（信息块和嵌入矩阵）。

        从 chunks.pkl 和 embeddings.npy 文件读取已缓存的索引，
        并验证两者数量是否一致。

        Returns:
            二元组 (chunks, embeddings)：信息块列表和嵌入矩阵；
            若文件不存在或数据不一致则返回 ([], None)。
        """
        if not (self.chunks_path.exists() and self.embeds_path.exists()):
            return [], None
        try:
            with self.chunks_path.open("rb") as file:
                chunks = pickle.load(file)
            embeddings = np.load(self.embeds_path)
            if len(chunks) != embeddings.shape[0]:
                return [], None
            return chunks, embeddings
        except Exception:
            return [], None

    @staticmethod
    def _manifest_reusable(manifest: dict, chunks: list, embeddings) -> bool:
        """验证已有清单与索引数据是否可复用。

        检查清单中记录的索引范围是否被当前加载的 chunks 和 embeddings 所覆盖。
        若清单为空则视为可复用（将从头构建）。

        Args:
            manifest: 文件记录清单字典。
            chunks: 已加载的信息块列表。
            embeddings: 已加载的嵌入矩阵（可为 None）。

        Returns:
            True 表示可复用，False 表示需要丢弃重建。
        """
        if not manifest:
            return True
        if embeddings is None:
            return False
        expected_total = 0
        for entry in manifest.values():
            start = int(entry.get("start", -1))
            end = int(entry.get("end", -1))
            n_chunks = int(entry.get("n_chunks", end - start))
            if start < 0 or end < start or n_chunks != end - start:
                return False
            expected_total = max(expected_total, end)
        return len(chunks) >= expected_total and embeddings.shape[0] >= expected_total

    def _save(self, chunks: list[GeneChunk], embeddings, manifest_files: dict):
        """将索引数据持久化到磁盘。

        保存信息块（pickle）、嵌入矩阵（npy）和清单（JSON）。
        若 embeddings 为 None 且旧嵌入文件存在则删除旧文件。

        Args:
            chunks: 信息块列表。
            embeddings: 嵌入矩阵（可为 None）。
            manifest_files: 文件记录字典，将写入 manifest.json。
        """
        with self.chunks_path.open("wb") as file:
            pickle.dump(chunks, file)
        if embeddings is not None:
            np.save(self.embeds_path, embeddings)
        elif self.embeds_path.exists():
            self.embeds_path.unlink()
        self.manifest_path.write_text(
            json.dumps(
                {"chunker_version": CHUNKER_VERSION, "files": manifest_files},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


@dataclass(frozen=True)
class IndexBuildResult:
    """索引构建结果摘要，记录一次索引构建操作的输出统计信息。

    Attributes:
        chunk_count: 构建后索引中的信息块总数。
        embedding_shape: 嵌入矩阵的形状（可为 None）。
        data_dir: 数据文件目录路径。
        index_dir: 索引存储目录路径。
    """

    chunk_count: int
    embedding_shape: tuple[int, ...] | None
    data_dir: Path
    index_dir: Path


class IndexService:
    """索引服务：提供标准化的索引构建接口。

    封装 IncrementalIndexer 的调用逻辑，作为外部模块调用索引功能的统一入口。

    Attributes:
        data_dir: 数据文件目录。
        index_dir: 索引存储目录。
        embed_texts: 文本嵌入函数。
        load_paper: 自定义论文加载函数。
        indexer_cls: 索引器类（默认为 IncrementalIndexer）。
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        index_dir: Path,
        embed_texts: Callable[[list[str]], np.ndarray],
        load_paper: Callable[[Path], list[Any]] | None = None,
        indexer_cls: type | None = None,
    ):
        """初始化索引服务。

        Args:
            data_dir: 包含待索引 JSON 文件的数据目录。
            index_dir: 索引文件的存储目录。
            embed_texts: 文本嵌入函数。
            load_paper: 自定义论文加载函数（可选）。
            indexer_cls: 自定义索引器类（可选，默认为 IncrementalIndexer）。
        """
        self.data_dir = Path(data_dir)
        self.index_dir = Path(index_dir)
        self.embed_texts = embed_texts
        self.load_paper = load_paper
        self.indexer_cls = indexer_cls

    def build(self, *, force: bool = False) -> IndexBuildResult:
        """执行索引构建。

        创建索引器实例并执行增量构建，返回构建结果摘要。

        Args:
            force: 若为 True 则强制完全重建，忽略缓存。

        Returns:
            IndexBuildResult 包含信息块数量和嵌入矩阵形状等信息。
        """
        indexer_cls = self.indexer_cls or IncrementalIndexer
        load_paper = self.load_paper or self._load_paper_chunks

        indexer = indexer_cls(
            index_dir=self.index_dir,
            data_dir=self.data_dir,
            embed_fn=self.embed_texts,
        )
        chunks, embeddings = indexer.build_incremental(
            force=force,
            load_paper_fn=load_paper,
        )
        return IndexBuildResult(
            chunk_count=len(chunks),
            embedding_shape=None if embeddings is None else tuple(embeddings.shape),
            data_dir=self.data_dir,
            index_dir=self.index_dir,
        )

    @staticmethod
    def _load_paper_chunks(path: Path) -> list[Any]:
        """加载论文 JSON 文件并进行分块处理。

        Args:
            path: 论文 JSON 文件路径。

        Returns:
            该论文的基因信息块列表。
        """
        with Path(path).open("r", encoding="utf-8") as file:
            paper = json.load(file)
        return chunk_paper(paper)


class RetrievalService:
    """检索服务：工具无关的检索接口，封装底层基因检索器。

    提供同步和异步两种搜索接口，支持混合搜索和重排序。

    Attributes:
        retriever: 底层检索器实例（如 JinaRetriever）。
    """

    def __init__(self, *, retriever: Any):
        """初始化检索服务。

        Args:
            retriever: 底层检索器实例。
        """
        self.retriever = retriever

    @property
    def total_chunks(self) -> int:
        """获取索引中的信息块总数。"""
        return len(getattr(self.retriever, "chunks", []))

    def build_index(self, *, force: bool = False, incremental: bool = True) -> None:
        """构建或更新索引。

        Args:
            force: 强制重建索引。
            incremental: 是否使用增量构建（当前参数未使用）。
        """
        self.retriever.build_index(force=force, incremental=incremental)

    def search_gene_chunks(
        self,
        query: str,
        *,
        top_k: int = 20,
        use_hybrid: bool = True,
        rerank: bool = True,
        rerank_top_n: int = 50,
    ):
        """同步搜索基因信息块。

        内部通过 asyncio.run 调用异步搜索方法。

        Args:
            query: 搜索查询文本。
            top_k: 返回的最大结果数，默认 20。
            use_hybrid: 是否使用混合搜索，默认 True。
            rerank: 是否使用重排序，默认 True。
            rerank_top_n: 重排序的候选数量，默认 50。

        Returns:
            (GeneChunk, score) 元组列表。
        """
        return asyncio.run(
            self.asearch_gene_chunks(
                query,
                top_k=top_k,
                use_hybrid=use_hybrid,
                rerank=rerank,
                rerank_top_n=rerank_top_n,
            )
        )

    async def asearch_gene_chunks(
        self,
        query: str,
        *,
        top_k: int = 20,
        use_hybrid: bool = True,
        rerank: bool = True,
        rerank_top_n: int = 50,
    ):
        """异步搜索基因信息块。

        优先使用混合搜索（向量+BM25+重排序），若检索器不支持则回退到普通向量搜索。

        Args:
            query: 搜索查询文本。
            top_k: 返回的最大结果数，默认 20。
            use_hybrid: 是否使用混合搜索，默认 True。
            rerank: 是否使用重排序，默认 True。
            rerank_top_n: 重排序的候选数量，默认 50。

        Returns:
            (GeneChunk, score) 元组列表。
        """
        if use_hybrid and hasattr(self.retriever, "hybrid_search"):
            return await self.retriever.hybrid_search(
                query,
                top_k=top_k,
                rerank=rerank,
                rerank_top_n=rerank_top_n,
            )
        return await asyncio.to_thread(self.retriever.search, query, top_k)
