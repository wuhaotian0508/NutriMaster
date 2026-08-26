from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from Bio import Entrez

logger = logging.getLogger(__name__)

_ENTREZ_EMAIL = "nutrimaster_rag@example.com"
_SPECIES_ABBREV_MAP = {
    "G. max": "Glycine max",
    "A. thaliana": "Arabidopsis thaliana",
    "O. sativa": "Oryza sativa",
    "Z. mays": "Zea mays",
    "N. tabacum": "Nicotiana tabacum",
    "T. aestivum": "Triticum aestivum",
}
_GENUS_MAP = {"G.": "Glycine", "A.": "Arabidopsis", "O.": "Oryza", "Z.": "Zea", "N.": "Nicotiana", "T.": "Triticum"}
_GENE_PREFIX_TO_SPECIES = {
    "Gm": "Glycine max",
    "At": "Arabidopsis thaliana",
    "Os": "Oryza sativa",
    "Zm": "Zea mays",
    "Nt": "Nicotiana tabacum",
    "Nb": "Nicotiana benthamiana",
    "Ta": "Triticum aestivum",
    "Sl": "Solanum lycopersicum",
    "St": "Solanum tuberosum",
    "Md": "Malus domestica",
    "Vv": "Vitis vinifera",
    "Br": "Brassica rapa",
    "Bn": "Brassica napus",
    "Cs": "Cucumis sativus",
    "Gh": "Gossypium hirsutum",
    "Pv": "Phaseolus vulgaris",
    "Lj": "Lotus japonicus",
    "Mt": "Medicago truncatula",
}


def _strip_species_prefix(gene_name: str) -> str | None:
    """去除基因名中的物种缩写前缀（如 Gm、At、Os 等）。

    Args:
        gene_name: 原始基因名（如 "GmFAD2"）。

    Returns:
        str | None: 去除前缀后的基因名（如 "FAD2"）；如果没有匹配的前缀，返回 None。
    """
    for prefix in sorted(_GENE_PREFIX_TO_SPECIES, key=len, reverse=True):
        if gene_name.startswith(prefix) and len(gene_name) > len(prefix):
            return gene_name[len(prefix):]
    return None


def _normalize_species_name(species: str) -> str:
    """将物种名称标准化为完整的拉丁学名。

    支持缩写形式（如 "G. max" -> "Glycine max"）和属名缩写（如 "O." -> "Oryza"）。
    如果已经是标准格式或无法识别，则原样返回。

    Args:
        species: 原始物种名称字符串。

    Returns:
        str: 标准化后的物种拉丁学名；输入为空时返回空字符串。
    """
    if not species:
        return ""
    species = species.strip()
    if species in _SPECIES_ABBREV_MAP:
        return _SPECIES_ABBREV_MAP[species]
    if re.match(r"^[A-Z][a-z]+ [a-z][a-zA-Z-]*$", species):
        return species
    if match := re.match(r"^([A-Z]\.)\s+([a-z][a-zA-Z-]*)$", species):
        genus = _GENUS_MAP.get(match.group(1))
        if genus:
            return f"{genus} {match.group(2)}"
    return species


def _esearch_gene(gene_name: str, species: str, retmax: int = 10) -> list[str]:
    """在 NCBI Gene 数据库中按基因名和物种搜索基因 ID。

    Args:
        gene_name: 基因名称。
        species: 物种拉丁学名，用于限定搜索范围。
        retmax: 最多返回的结果数量，默认 10。

    Returns:
        list[str]: 匹配的 NCBI Gene ID 列表。
    """
    query = f'"{gene_name}"[Gene Name]'
    if species:
        query += f' AND "{species}"[Organism]'
    with Entrez.esearch(db="gene", term=query, retmax=retmax) as handle:
        record = Entrez.read(handle)
    return record.get("IdList", [])


def _search_gene_ids(gene_name: str, species: str, retmax: int = 10) -> list[str]:
    """多策略搜索 NCBI Gene 数据库中的基因 ID。

    依次尝试：精确基因名搜索 -> 去除物种前缀后搜索 -> 宽泛关键词搜索。

    Args:
        gene_name: 基因名称（可能包含物种前缀）。
        species: 物种拉丁学名。
        retmax: 最多返回的结果数量，默认 10。

    Returns:
        list[str]: 匹配的 NCBI Gene ID 列表。
    """
    ids = _esearch_gene(gene_name, species, retmax)
    if ids:
        return ids
    if short_name := _strip_species_prefix(gene_name):
        ids = _esearch_gene(short_name, species, retmax)
        if ids:
            return ids
    query = f'"{gene_name}"'
    if species:
        query += f' AND "{species}"[Organism]'
    with Entrez.esearch(db="gene", term=query, retmax=retmax) as handle:
        return Entrez.read(handle).get("IdList", [])


def _link_gene_to_nuccore(gene_id: str) -> list[str]:
    """通过 NCBI Entrez elink 将 Gene ID 关联到核酸数据库（nuccore）记录。

    Args:
        gene_id: NCBI Gene 数据库的基因 ID。

    Returns:
        list[str]: 关联的核酸数据库记录 ID 列表。
    """
    with Entrez.elink(dbfrom="gene", db="nuccore", id=gene_id) as handle:
        record = Entrez.read(handle)
    return [
        link["Id"]
        for linksetdb in record[0].get("LinkSetDb", [])
        for link in linksetdb.get("Link", [])
    ]


def _fetch_nuccore_summaries(nuccore_ids: list[str]) -> list[dict]:
    """批量获取核酸数据库记录的摘要信息（accession 和标题）。

    Args:
        nuccore_ids: NCBI 核酸数据库记录 ID 列表。

    Returns:
        list[dict]: 包含 "accession" 和 "title" 键的字典列表；输入为空时返回空列表。
    """
    if not nuccore_ids:
        return []
    with Entrez.esummary(db="nuccore", id=",".join(nuccore_ids)) as handle:
        summary = Entrez.read(handle)
    return [{"accession": item.get("AccessionVersion", ""), "title": item.get("Title", "")} for item in summary]


def _direct_nuccore_search(gene_name: str, species: str, retmax: int = 10) -> list[dict]:
    """直接在 NCBI 核酸数据库中按基因名和物种搜索序列记录。

    当通过 Gene -> nuccore 关联路径无法找到结果时，作为备选方案使用。

    Args:
        gene_name: 基因名称。
        species: 物种拉丁学名。
        retmax: 最多返回的结果数量，默认 10。

    Returns:
        list[dict]: 包含 "accession" 和 "title" 键的字典列表。
    """
    query = f'"{gene_name}"'
    if species:
        query += f' AND "{species}"[Organism]'
    with Entrez.esearch(db="nuccore", term=query, retmax=retmax) as handle:
        ids = Entrez.read(handle).get("IdList", [])
    return _fetch_nuccore_summaries(ids)


def _pick_best_accession(records: list[dict], gene_name: str) -> str:
    """从多条核酸记录中选择最佳的 accession 编号。

    优先选择标题中包含基因名的记录，其次优先选择 mRNA/cDNA/transcript 类型的记录。

    Args:
        records: 核酸记录字典列表，每个字典包含 "accession" 和 "title"。
        gene_name: 基因名称，用于匹配标题。

    Returns:
        str: 最佳匹配的 accession 编号；如果记录列表为空，返回空字符串。
    """
    if not records:
        return ""
    gene_name_lower = gene_name.lower()
    exact_gene = [record for record in records if gene_name_lower in record.get("title", "").lower()]
    if exact_gene:
        transcript_like = [
            record
            for record in exact_gene
            if any(key in record.get("title", "").lower() for key in ("mrna", "cdna", "transcript"))
        ]
        return (transcript_like[0] if transcript_like else exact_gene[0]).get("accession", "")
    transcript_like = [
        record
        for record in records
        if any(key in record.get("title", "").lower() for key in ("mrna", "cdna", "transcript"))
    ]
    return (transcript_like[0] if transcript_like else records[0]).get("accession", "")


def _find_accession_for_gene(gene_name: str, species: str, pause: float = 0.34) -> str:
    """为单个基因查找最佳的 NCBI 核酸 accession 编号。

    完整查找流程：搜索 Gene ID -> 关联 nuccore 记录 -> 选择最佳 accession。
    如果关联路径无结果，回退到直接核酸数据库搜索。每次 API 调用间有短暂停顿以避免限流。

    Args:
        gene_name: 基因名称。
        species: 物种名称（会自动标准化）。
        pause: 每次 NCBI API 调用之间的等待秒数，默认 0.34 秒。

    Returns:
        str: 最佳匹配的 accession 编号；找不到时返回空字符串。
    """
    normalized_species = _normalize_species_name(species)
    gene_ids = _search_gene_ids(gene_name, normalized_species)
    time.sleep(pause)
    records = []
    for gene_id in gene_ids:
        nuccore_ids = _link_gene_to_nuccore(gene_id)
        time.sleep(pause)
        if nuccore_ids:
            records.extend(_fetch_nuccore_summaries(nuccore_ids))
            time.sleep(pause)
    accession = _pick_best_accession(records, gene_name)
    if accession:
        return accession
    direct_records = _direct_nuccore_search(gene_name, normalized_species)
    time.sleep(pause)
    return _pick_best_accession(direct_records, gene_name)


def run_gene2accession(genes: list[dict], work_dir: Path) -> list[Path]:
    """批量查询基因列表对应的 NCBI 核酸 accession 编号，按物种分别写入文件。

    对每个基因调用 NCBI Entrez API 查找 accession，结果以 TSV 格式（基因名\\t物种\\taccession）
    按物种写入 work_dir/{species}_accession.txt 文件（物种名中空格替换为下划线）。

    Args:
        genes: 基因信息字典列表，每个字典需包含 "gene"（基因名）和 "species"（物种名）。
        work_dir: 工作目录，输出文件将写入此目录。

    Returns:
        list[Path]: 生成的各物种 accession 文件路径列表。

    Raises:
        ValueError: 所有基因均未能找到 accession 时抛出。
    """
    Entrez.email = _ENTREZ_EMAIL
    results = []
    for gene in genes:
        normalized_species = _normalize_species_name(gene["species"])
        try:
            accession = _find_accession_for_gene(gene["gene"], gene["species"])
        except MemoryError:
            raise
        except Exception as exc:
            logger.warning("Gene %s accession lookup failed: %s", gene["gene"], exc)
            accession = ""
        results.append((gene["gene"], normalized_species, accession))

    if not any(result[2] for result in results):
        raise ValueError("未能为任何基因找到 NCBI accession")

    species_groups: dict[str, list[tuple[str, str, str]]] = {}
    for row in results:
        species_groups.setdefault(row[1], []).append(row)

    output_files = []
    for species, rows in species_groups.items():
        filename = species.replace(" ", "_") + "_accession.txt"
        acc_file = work_dir / filename
        with acc_file.open("w", encoding="utf-8") as file:
            for gene_name, sp, accession in rows:
                file.write(f"{gene_name}\t{sp}\t{accession}\n")
        output_files.append(acc_file)

    return output_files


__all__ = [
    "_normalize_species_name",
    "_search_gene_ids",
    "run_gene2accession",
]
