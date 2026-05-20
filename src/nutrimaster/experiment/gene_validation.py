from __future__ import annotations

import re
from typing import Any

_GENE_NAME_RE = re.compile(r"\b[A-Z][a-z]{1,2}[A-Z][A-Za-z]{0,10}\d{0,3}[A-Za-z]?\b")
_TOOL_NAME_BLACKLIST = {
    "SpCas9",
    "SaCas9",
    "FnCas12a",
    "LbCpf1",
    "AsCpf1",
    "CasRx",
    "dCas9",
    "nCas9",
    "Cas12a",
    "Cas13a",
    "PubMed",
}
_VAGUE_SUFFIXES = ("之类", "之类的", "等", "等等", "类似")


def has_gene_names(text: str) -> bool:
    """判断文本中是否包含可识别的基因名称。

    Args:
        text: 待检测的文本字符串。

    Returns:
        bool: 如果文本中包含至少一个有效基因名称则返回 True，否则返回 False。
    """
    return bool(extract_gene_names(text))


def extract_gene_names(text: str) -> list[str]:
    """从文本中提取基因名称列表。

    使用正则表达式匹配基因命名模式（如 OsNAS1、ZmPSY1 等），
    并排除 CRISPR 工具名称（如 SpCas9）和带有模糊后缀（如"等""之类"）的匹配项。
    结果按出现顺序去重。

    Args:
        text: 待提取基因名称的文本字符串。

    Returns:
        list[str]: 去重后的基因名称列表，按首次出现的顺序排列。
    """
    seen = set()
    names = []
    for match in _GENE_NAME_RE.finditer(text):
        name = match.group()
        if name in _TOOL_NAME_BLACKLIST:
            continue
        suffix = text[match.end():]
        if any(suffix.startswith(item) for item in _VAGUE_SUFFIXES):
            continue
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def extract_transgenic_species_with_llm(answer_text: str) -> list[str]:
    """使用 LLM 从对话文本中推断需要外源转入目的基因的受体物种。

    分析文本中的实验场景，识别哪个物种是目标宿主（即接受外源基因的物种）。
    适用场景包括：途径重构（如"在烟草中重构喜树碱合成途径"）、性状改良
    （如"通过转基因提高大豆中omega-3含量"）、功能验证（如"在拟南芥中过表达GmFAD2"）。

    Args:
        answer_text: 包含实验背景和物种信息的文本（通常来自用户问题+RAG系统回答）。

    Returns:
        list[str]: 推断出的受体物种拉丁名列表；无法推断时返回空列表。
    """
    from nutrimaster.config.llm import call_llm_sync
    import json

    prompt = (
        "你是一名植物代谢领域专家。请从以下文本中识别需要转基因的受体物种，并返回拉丁名。\n\n"
        "【受体物种判断规则】\n"
        "受体物种是接受外源基因、进行转化或功能验证的目标植物，常见场景：\n"
        "- 途径重构：'在烟草中重构喜树碱生物合成途径' → 受体：Nicotiana tabacum\n"
        "- 性状改良：'通过转基因提高大豆中omega-3含量' → 受体：Glycine max\n"
        "- 功能验证：'在拟南芥中过表达GmFAD2' → 受体：Arabidopsis thaliana\n"
        "- 异源表达：'将SmCPS1转入本氏烟草' → 受体：Nicotiana benthamiana\n"
        "- 代谢工程：'改造水稻以积累更多类胡萝卜素' → 受体：Oryza sativa\n\n"
        "【注意】\n"
        "- 如果文本同时提到多个物种，只选作为宿主/受体的那个或那些（通常是'转入/过表达/重构于'后面的物种）\n"
        "- 基因来源物种不是受体（如'将大豆基因GmFAD2转入烟草'中大豆是来源，烟草是受体）\n"
        "- 返回标准拉丁双名法，如 Nicotiana tabacum，不要用中文\n\n"
        '返回 JSON 数组，仅包含物种拉丁名字符串，例如：["Nicotiana tabacum"]\n'
        "如果无法判断受体物种，返回空数组 []。\n\n"
        f"文本：\n{answer_text}"
    )
    try:
        raw = call_llm_sync([{"role": "user", "content": prompt}], temperature=0).content
        text = raw.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        if isinstance(result, list):
            return [s for s in result if isinstance(s, str) and s.strip()]
    except Exception:
        pass
    return []


def verify_genes_with_ncbi(genes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """通过 NCBI 数据库验证基因列表的有效性。

    对每个基因调用 NCBI Entrez 接口，根据基因名称和物种信息
    搜索对应的基因 ID，返回验证结果（是否在 NCBI 中找到、
    对应的基因 ID 列表等）。

    Args:
        genes: 基因信息字典列表，每个字典应包含：
            - "gene" (str): 基因名称。
            - "species" (str): 物种名称。

    Returns:
        list[dict[str, Any]]: 验证结果列表，每个字典包含：
            - "gene" (str): 基因名称。
            - "species" (str): 标准化后的物种名称。
            - "ncbi_found" (bool): 是否在 NCBI 中找到该基因。
            - "gene_ids" (list): NCBI 基因 ID 列表（最多 3 个）。
    """
    from Bio import Entrez
    from nutrimaster.experiment.crispr.gene2accession import _normalize_species_name, _search_gene_ids

    Entrez.email = "nutrimaster_rag@example.com"
    verified = []
    for gene in genes:
        species = _normalize_species_name(gene.get("species", ""))
        gene_name = gene.get("gene", "")
        try:
            gene_ids = _search_gene_ids(gene_name, species)
        except Exception:
            gene_ids = []
        verified.append(
            {
                "gene": gene_name,
                "species": species,
                "ncbi_found": bool(gene_ids),
                "gene_ids": gene_ids[:3],
            }
        )
    return verified
