from __future__ import annotations

import re
import argparse
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from Bio import Entrez, SeqIO

logger = logging.getLogger(__name__)

ENTREZ_EMAIL = "nutrimaster_rag@example.com"

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


@dataclass
class GeneLocation:
    gene: str
    species: str
    gene_id: str
    chr_accession: str
    start: int
    stop: int
    strand: int  # 1 = plus, 2 = minus


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


def _get_gene_location(gene_name: str, species: str) -> GeneLocation:
    """
    通过基因名和物种获取该基因在基因组上的位置。

    注意：
    NCBI Gene esummary 中 ChrStart / ChrStop 通常是 0-based 坐标；
    这里转换为 efetch 需要的 1-based inclusive 坐标。
    """
    normalized_species = _normalize_species_name(species)
    gene_ids = _search_gene_ids(gene_name, normalized_species, retmax=10)

    if not gene_ids:
        raise ValueError(f"未找到 Gene ID: {gene_name}, {normalized_species}")

    for gene_id in gene_ids:
        with Entrez.esummary(db="gene", id=gene_id) as handle:
            summary = Entrez.read(handle)

        docsum = summary["DocumentSummarySet"]["DocumentSummary"][0]
        genomic_info = docsum.get("GenomicInfo", [])

        if not genomic_info:
            logger.warning("Gene ID %s 没有 GenomicInfo，跳过", gene_id)
            continue

        # 通常第一条是主要基因组位置
        info = genomic_info[0]

        chr_accession = info.get("ChrAccVer", "")
        chr_start = int(info["ChrStart"])
        chr_stop = int(info["ChrStop"])

        if not chr_accession:
            continue

        # NCBI Gene 坐标可能正链 start < stop，负链 start > stop
        strand = 1 if chr_start <= chr_stop else 2

        # 转换成 1-based inclusive 坐标
        start = min(chr_start, chr_stop) + 1
        stop = max(chr_start, chr_stop) + 1

        return GeneLocation(
            gene=gene_name,
            species=normalized_species,
            gene_id=gene_id,
            chr_accession=chr_accession,
            start=start,
            stop=stop,
            strand=strand,
        )

    raise ValueError(f"找到 Gene ID，但未找到可用的基因组坐标: {gene_name}, {normalized_species}")


def _fetch_region(
    accession: str,
    start: int,
    stop: int,
    strand: int = 1,
    pause: float = 0.34,
):
    """
    从 NCBI nuccore 获取指定基因组区间序列。

    start / stop 为 1-based inclusive 坐标。
    strand=1 获取正链；
    strand=2 获取反向互补链。
    """
    if start < 1:
        start = 1

    if stop < start:
        return None

    time.sleep(pause)

    with Entrez.efetch(
        db="nuccore",
        id=accession,
        rettype="fasta",
        retmode="text",
        seq_start=start,
        seq_stop=stop,
        strand=strand,
    ) as handle:
        records = list(SeqIO.parse(handle, "fasta"))

    if not records:
        return None

    return records[0]


def get_gene_flanks(
    gene_name: str,
    species: str,
    upstream: int = 2000,
    downstream: int = 1000,
    pause: float = 0.34,
) -> dict:
    """
    获取某基因的上游、基因本体、下游序列。

    返回值包括：
    - gene_location: 基因组坐标信息
    - upstream_record: 上游序列 SeqRecord
    - gene_record: 基因本体序列 SeqRecord
    - downstream_record: 下游序列 SeqRecord

    对负链基因：
    - upstream 指转录方向上游，即基因组坐标较大的方向
    - downstream 指转录方向下游，即基因组坐标较小的方向
    - gene / upstream / downstream 返回序列均按基因转录方向输出
    """
    location = _get_gene_location(gene_name, species)

    if location.strand == 1:
        upstream_start = max(1, location.start - upstream)
        upstream_stop = location.start - 1

        gene_start = location.start
        gene_stop = location.stop

        downstream_start = location.stop + 1
        downstream_stop = location.stop + downstream

        fetch_strand = 1

    else:
        # 负链基因的转录方向与基因组坐标方向相反
        upstream_start = location.stop + 1
        upstream_stop = location.stop + upstream

        gene_start = location.start
        gene_stop = location.stop

        downstream_start = max(1, location.start - downstream)
        downstream_stop = location.start - 1

        # 取反向互补，使输出序列方向与基因转录方向一致
        fetch_strand = 2

    upstream_record = _fetch_region(
        accession=location.chr_accession,
        start=upstream_start,
        stop=upstream_stop,
        strand=fetch_strand,
        pause=pause,
    )

    gene_record = _fetch_region(
        accession=location.chr_accession,
        start=gene_start,
        stop=gene_stop,
        strand=fetch_strand,
        pause=pause,
    )

    downstream_record = _fetch_region(
        accession=location.chr_accession,
        start=downstream_start,
        stop=downstream_stop,
        strand=fetch_strand,
        pause=pause,
    )

    if upstream_record:
        upstream_record.id = (
            f"{gene_name}|upstream|{upstream}bp|"
            f"{location.chr_accession}:{upstream_start}-{upstream_stop}"
        )
        upstream_record.description = (
            f"{gene_name} upstream {upstream} bp, "
            f"{location.species}, GeneID={location.gene_id}, "
            f"strand={location.strand}"
        )

    if gene_record:
        gene_record.id = (
            f"{gene_name}|gene|"
            f"{location.chr_accession}:{gene_start}-{gene_stop}"
        )
        gene_record.description = (
            f"{gene_name} gene sequence, "
            f"{location.species}, GeneID={location.gene_id}, "
            f"strand={location.strand}"
        )

    if downstream_record:
        downstream_record.id = (
            f"{gene_name}|downstream|{downstream}bp|"
            f"{location.chr_accession}:{downstream_start}-{downstream_stop}"
        )
        downstream_record.description = (
            f"{gene_name} downstream {downstream} bp, "
            f"{location.species}, GeneID={location.gene_id}, "
            f"strand={location.strand}"
        )

    return {
        "gene_location": location,
        "upstream_record": upstream_record,
        "gene_record": gene_record,
        "downstream_record": downstream_record,
    }

def write_gene_flanks(
    genes: list[dict],
    output_fasta: Path,
    upstream: int = 2000,
    downstream: int = 1000,
) -> Path:
    """
    批量获取多个基因的上下游序列，并写入一个 FASTA 文件。

    genes 示例：
    [
        {"gene": "GmFAD2", "species": "G. max"},
        {"gene": "AtFAD2", "species": "A. thaliana"},
    ]
    """
    Entrez.email = ENTREZ_EMAIL

    records = []

    for item in genes:
        gene_name = item["gene"]
        species = item["species"]

        try:
            result = get_gene_flanks(
                gene_name=gene_name,
                species=species,
                upstream=upstream,
                downstream=downstream,
            )

            location = result["gene_location"]
            logger.info(
                "%s %s GeneID=%s %s:%s-%s strand=%s",
                gene_name,
                location.species,
                location.gene_id,
                location.chr_accession,
                location.start,
                location.stop,
                location.strand,
            )

            if result["upstream_record"]:
                records.append(result["upstream_record"])

            if result["gene_record"]:
                records.append(result["gene_record"])

            if result["downstream_record"]:
                records.append(result["downstream_record"])

        except Exception as exc:
            logger.warning("获取 %s 上下游序列失败: %s", gene_name, exc)

    if not records:
        raise ValueError("未能获取任何上下游序列")

    output_fasta.parent.mkdir(parents=True, exist_ok=True)
    SeqIO.write(records, output_fasta, "fasta")

    return output_fasta


@dataclass
class GeneSequenceResult:
    """单个基因的序列结果，供转基因 SOP 生成使用。"""

    gene: str
    species: str
    accession: str          # 基因组染色体 accession
    gene_seq: str           # 基因本体序列
    upstream_seq: str       # 上游 2000 bp 序列
    downstream_seq: str     # 下游 2000 bp 序列


def run_gene_transfer_sequences(
    genes: list[dict],
    upstream: int = 2000,
    downstream: int = 2000,
) -> list[GeneSequenceResult]:
    """批量获取多个基因的序列及上下游序列，供转基因 SOP 生成使用。

    Args:
        genes: 基因信息字典列表，每个字典包含 "gene" 和 "species" 键。
            示例：[{"gene": "GmFAD2", "species": "Glycine max"}]
        upstream: 上游序列长度，默认 2000 bp。
        downstream: 下游序列长度，默认 2000 bp。

    Returns:
        list[GeneSequenceResult]: 每个基因对应的序列结果列表。
            失败的基因将被跳过并记录警告日志。

    Raises:
        ValueError: 所有基因均获取失败时抛出。
    """
    Entrez.email = ENTREZ_EMAIL
    results: list[GeneSequenceResult] = []

    for item in genes:
        gene_name = item["gene"]
        species = item.get("species", "")

        try:
            flanks = get_gene_flanks(
                gene_name=gene_name,
                species=species,
                upstream=upstream,
                downstream=downstream,
            )
            location = flanks["gene_location"]

            gene_seq = str(flanks["gene_record"].seq) if flanks["gene_record"] else ""
            up_seq = str(flanks["upstream_record"].seq) if flanks["upstream_record"] else ""
            down_seq = str(flanks["downstream_record"].seq) if flanks["downstream_record"] else ""

            results.append(
                GeneSequenceResult(
                    gene=gene_name,
                    species=location.species,
                    accession=location.chr_accession,
                    gene_seq=gene_seq,
                    upstream_seq=up_seq,
                    downstream_seq=down_seq,
                )
            )
        except Exception as exc:
            logger.warning("获取 %s 序列失败: %s", gene_name, exc)

    if not results:
        raise ValueError("未能获取任何基因序列")

    return results


def read_gene_table(input_file: Path) -> list[dict]:
    """
    读取 TSV 文件。

    输入文件格式：
    gene<TAB>species

    示例：
    GmFAD2<TAB>G. max
    AtFAD2<TAB>A. thaliana
    """
    genes = []

    with input_file.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) < 2:
                raise ValueError(f"输入行格式错误，应为 gene<TAB>species: {line}")

            genes.append({"gene": parts[0], "species": parts[1]})

    return genes


def main():
    global ENTREZ_EMAIL

    parser = argparse.ArgumentParser(
        description="通过基因名和物种获取该基因在基因组上的上下游序列"
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="输入 TSV 文件，格式：gene<TAB>species",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="gene_flanks.fasta",
        help="输出 FASTA 文件",
    )
    parser.add_argument(
        "--upstream",
        type=int,
        default=2000,
        help="上游长度，默认 2000 bp",
    )
    parser.add_argument(
        "--downstream",
        type=int,
        default=1000,
        help="下游长度，默认 1000 bp",
    )
    parser.add_argument(
        "--email",
        default=ENTREZ_EMAIL,
        help="NCBI Entrez email",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    ENTREZ_EMAIL = args.email
    Entrez.email = args.email

    genes = read_gene_table(Path(args.input))

    output = write_gene_flanks(
        genes=genes,
        output_fasta=Path(args.output),
        upstream=args.upstream,
        downstream=args.downstream,
    )

    print(f"完成，结果已写入: {output}")


if __name__ == "__main__":
    main()