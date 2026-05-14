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
    from nutrimaster.crispr.gene2accession import _normalize_species_name, _search_gene_ids

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
