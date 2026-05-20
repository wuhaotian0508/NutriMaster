from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from pathlib import Path

import requests
from lxml import etree

logger = logging.getLogger(__name__)

_URL = "http://crispr.hzau.edu.cn/cgi-bin/CRISPR2/CRISPR"
_TIMEOUT = 30
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
}
_OUTPUT_COLUMNS = [
    "Species", "Gene", "Accession",
    "Seq_name", "On_score", "Target_number", "Sequence", "PAM", "Region", "%GC", "Sequence_RC",
    "Recommended",
]

crispr_p_db = {
    "Actinidia chinensis": "Actinidia chinensis var. chinensis",
    "Ananas comosus": "Ananas comosus",
    "Arabidopsis lyrata": "Arabidopsis lyrata (v.1.0)",
    "Arabidopsis thaliana": "Arabidopsis thaliana (TAIR10)",
    "Arachis duranensis": "Arachis duranensis(v1.0)",
    "Arachis ipaensis": "Arachis ipaensis(v1.0)",
    "Betula pendula": "Betula pendula subsp. pendula(1.4c Pseudochromosomes)",
    "Brachypodium distachyon": "Brachypodium distachyon (v1.0)",
    "Brassica juncea": "Brassica juncea(v1.5)",
    "Brassica napus Xiaoyun": "Brassica napus (Xiaoyun)",
    "Brassica napus": "Brassica napus (v4.1)",
    "Brassica napus ganganF73": "Brassica napus(ganganF73.v0)",
    "Brassica napus no2127": "Brassica napus(no2127.v0)",
    "Brassica napus quintaA": "Brassica napus(quintaA.v0)",
    "Brassica napus shengli3": "Brassica napus(shengli3.v0)",
    "Brassica napus tapidor3": "Brassica napus(tapidor3.v0)",
    "Brassica napus westar": "Brassica napus(westar.v0)",
    "Brassica napus zheyou73": "Brassica napus(zheyou73.v0)",
    "Brassica napus zs11": "Brassica napus(zs11.v0)",
    "Brassica oleracea": "Brassica oleracea (v1.0)",
    "Brassica rapa": "Brassica rapa (IVFCAASv1)",
    "Capsella rubella": "Capsella rubella(v1.0 )",
    "Ceratobasidium sp AG Ba": "Ceratobasidium sp. AG-Ba",
    "Chlamydomonas reinhardtii V5": "Chlamydomonas reinhardtii (v5.5)",
    "Watermelon": "Citrullus lanatus (v1.0)",
    "Citrus sinensis": "Citrus sinensis (v2.0)",
    "Coffea canephora": "Coffea canephora",
    "Cucumis melo": "Cucumis melo (v3.5)",
    "Csativus 122": "Cucumis sativus",
    "Cyanidioschyzon merolae": "Cyanidioschyzon merolae (ASM9120v1)",
    "Diospyros kaki": "Diospyros kaki(persimmon)",
    "Fortunella hindsii": "Fortunella hindsii",
    "Fragaria vesca": "Fragaria vesca(v2.0.a1)",
    "Glycine max": "Glycine max (V1.0)",
    "Gossypium barbadense": "Gossypium barbadense (v1.1)",
    "Gossypium hirsutum": "Gossypium hirsutum (v1.1)",
    "Gossypium hirsutum v21": "Gossypium hirsutum (v2.1)",
    "Graimondii 221": "Gossypium raimondii",
    "Lactuca sativa": "Lactuca sativa (v8)",
    "Lentinus edodes": "Lentinula edodes(W1-26)",
    "Lentinus edodes B17": "Lentinula edodes(B17)",
    "Lotus japonicus": "Lotus japonicus (v3.0)",
    "Malus x domestica": "Malus x domestica (GDDH13 v1.1)",
    "Manihot esculenta": "Manihot esculenta (v6.1)",
    "Marchantia polymorpha": "Marchantia polymorpha(v3.1)",
    "Medicago ruthenica": "Medicago ruthenica",
    "Medicago sativa L": "Medicago sativa L.",
    "Medicago truncatula": "Medicago truncatula (Mt4.0v2)",
    "Musa acuminata": "Musa acuminata (MA1)",
    "Nicotiana benthamiana": "Nicotiana benthamiana (v0.4.4)",
    "Oryza brachyantha": "Oryza brachyantha (v1.4b)",
    "Oryza glaberrima": "Oryza glaberrima (AGI1.1)",
    "Oryza indica": "Oryza indica (ASM465v1)",
    "Oryza indica MH63": "Oryza indica (MH63)",
    "Oryza indica ZS97": "Oryza indica (ZS97)",
    "Oryza sativa": "Oryza sativa (RAP-DB)",
    "Oryza sativa MSU": "Oryza sativa(MSU)",
    "Panicum virgatum": "Panicum virgatum(v1.1)",
    "Panicum virgatum v51": "Panicum virgatum(v5.1)",
    "Petunia axillaris": "Petunia axillaris(v1.6.2)",
    "Petunia inflata": "Petunia inflata(v1.0.1)",
    "Physcomitrella patens": "Physcomitrella patens (ASM242v1)",
    "Populus trichocarpa": "Populus trichocarpa (JGI2.0)",
    "Prunus avium": "Prunus avium (v.1.0)",
    "kasalath RAP": "Rice kasalath",
    "Ricinus communis": "Ricinus communis",
    "Salvia miltiorrhiza": "Salvia miltiorrhiza",
    "Selaginella moellendorffii": "Selaginella moellendorffii (v1.0)",
    "Setaria italica": "Setaria italica (JGIv2.0)",
    "Setaria viridis": "Setaria viridis (v1.1)",
    "Solanum lycopersicum": "Solanum lycopersicum (SL2.50) ",
    "Solanum lycopersicum 30": "Solanum lycopersicum (SL3.0) ",
    "Solanum tuberosum": "Solanum tuberosum (3.0)",
    "Sorghum bicolor": "Sorghum bicolor (Sorbi1)",
    "Thlaspi arvense": "Thlaspi arvense(v1.1)",
    "Triticum aestivum": "Triticum aestivum (IWGSC, chromosome.1)",
    "Utricularia gibba": "Utricularia gibba",
    "Vaccinium corymbosum": "Vaccinium corymbosum(v1.0)",
    "Vigna unguiculata": "Vigna unguiculata (v1.1)",
    "Vitis vinifera": "Vitis vinifera (IGGP 12x)",
    "Zea mays": "Zea mays (AGPv3.21)",
    "Zea mays v4": "Zea mays (AGPv4)"
}

def _parse_fasta(path: Path) -> Iterator[tuple[str, str]]:
    """解析 FASTA 格式文件，逐条生成序列名称和序列内容。

    Args:
        path: FASTA 文件的路径。

    Yields:
        tuple[str, str]: (序列名称, 经过验证的核酸序列字符串)。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
        ValueError: FASTA 格式错误（如序列内容出现在 header 前）或文件为空时抛出。
    """
    if not path.exists():
        raise FileNotFoundError(f"FASTA 文件不存在: {path}")
    header = None
    sequence_lines: list[str] = []
    with path.open(encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    yield header, _validate_sequence("".join(sequence_lines), header)
                header = line[1:].strip() or f"record_at_line_{line_number}"
                sequence_lines = []
            elif header is None:
                raise ValueError(f"FASTA 格式错误：第 {line_number} 行在 header 前出现序列内容。")
            else:
                sequence_lines.append(line)
    if header is None:
        raise ValueError("FASTA 文件为空，或未找到有效记录。")
    yield header, _validate_sequence("".join(sequence_lines), header)


def _validate_sequence(sequence: str, name: str) -> str:
    """验证核酸序列是否仅包含合法字符（A/C/G/T/N），并去除空格。

    Args:
        sequence: 待验证的原始序列字符串。
        name: 序列名称，用于错误信息展示。

    Returns:
        str: 去除空格后的合法序列字符串。

    Raises:
        ValueError: 序列为空或包含非法字符时抛出。
    """
    cleaned = sequence.strip().replace(" ", "")
    if not cleaned:
        raise ValueError(f"序列为空: {name}")
    invalid = sorted(set(cleaned) - set("ACGTNacgtn"))
    if invalid:
        raise ValueError(f"序列 {name} 含有非法字符: {', '.join(invalid)}；仅允许 A/C/G/T/N")
    return cleaned


def _reverse_complement(sequence: str) -> str:
    """计算核酸序列的反向互补序列。

    将 A<->T、C<->G、N<->N 互补配对后反转序列顺序，同时保持大小写。

    Args:
        sequence: 输入的核酸序列字符串。

    Returns:
        str: 反向互补后的序列字符串。
    """
    return sequence.translate(str.maketrans("ACGTNacgtn", "TGCANtgcan"))[::-1]


def _fetch_result_page(sequence: str, species: str = "") -> str:
    """将核酸序列提交至 CRISPR-P 在线平台，获取 CRISPR 靶点设计结果页面。

    使用 SpCas9 的 NGG PAM、20bp spacer 长度进行靶点预测。

    Args:
        sequence: 待分析的核酸序列字符串。

    Returns:
        str: 服务器返回的 HTML 结果页面文本。

    Raises:
        RuntimeError: HTTP 请求失败时抛出。
    """
    payload = {
        "pam": "NGG",
        "oligo": "U3",
        "template": "GUUUUAGAGCUAGAAAUAGCAAGUUAAAAUAAGGCUAGUCCGUUAUCAACUUGAAAAAGUGGCACCGAGUCGGUGCUUUU",
        "spacer_length": 20,
        "name_db": "Actinidia_chinensis",
        "loc": "CEY00_Acc00114",
        "position": "CM009654.1:41843..42575",
        "sequence": sequence,
        ".submit": "Submit",
        ".cgifields": "oligo",
    }
    try:
        response = requests.post(_URL, data=payload, headers=_HEADERS, timeout=_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"请求失败: {exc}") from exc
    return response.text


def _extract_rows(html_text: str) -> list[dict[str, str]]:
    """从 CRISPR-P 返回的 HTML 页面中解析 CRISPR 靶点信息表格行。

    提取每个靶点的编号、On-score 得分、序列、PAM、区域、GC 含量，并计算反向互补序列。

    Args:
        html_text: CRISPR-P 平台返回的 HTML 文本。

    Returns:
        list[dict[str, str]]: 靶点信息字典列表，每个字典包含 Target_number、On_score、
            Sequence、PAM、Region、%GC、Sequence_RC 字段。

    Raises:
        ValueError: HTML 解析失败时抛出。
    """
    html = etree.HTML(html_text, parser=etree.HTMLParser())
    if html is None:
        raise ValueError("HTML 解析失败。")
    rows = html.xpath('//tr[contains(@class, "guideMouseOver") and contains(@class, "seqFmt")]')
    results = []
    for row in rows:
        texts = [item.strip() for item in row.xpath("./td//text()") if item.strip()]
        if len(texts) < 6:
            continue
        target_number, on_score, sequence, pam, region, gc = texts[:6]
        results.append(
            {
                "Target_number": target_number,
                "On_score": on_score,
                "Sequence": sequence,
                "PAM": pam,
                "Region": region,
                "%GC": gc,
                "Sequence_RC": _reverse_complement(sequence),
            }
        )
    return results



def _parse_fasta_header(header: str) -> tuple[str, str, str]:
    """从 fasta header 中解析 species、gene、accession。

    header 格式为 Glycine_max_GmMYB4_NM_XXXXXX，物种名为前两个下划线分隔字段。

    Returns:
        tuple[str, str, str]: (species, gene, accession)，物种名中下划线还原为空格。
    """
    parts = header.split("_")
    if len(parts) < 3:
        return header, "", ""
    species = f"{parts[0]} {parts[1]}"
    gene = parts[2]
    accession = "_".join(parts[3:]) if len(parts) > 3 else ""
    return species, gene, accession


def _slice_to_first_recommended(rows: list[dict], recommended_idx: int | None) -> list[dict]:
    """截取从第一行到第一个推荐靶点之后 3 行的所有行。"""
    if recommended_idx is None:
        return rows
    return rows[: min(recommended_idx + 4, len(rows))]


def _write_table(path: Path, rows: list[dict]) -> None:
    """将靶点数据写入 TSV 文件。"""
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=_OUTPUT_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def run_crispr_target(fasta_files: list[Path], work_dir: Path) -> list[Path]:
    """批量对各物种 FASTA 文件中的序列进行 CRISPR 靶点设计，按物种分别输出 TSV。

    每条序列的所有靶点均标注 Recommended 列（Yes/No），输出行范围为第一行到第一个
    推荐靶点之后 3 行。每个物种结果写入 work_dir/{species}_crispr_target.tsv。

    Args:
        fasta_files: accession2sequence 生成的各物种 FASTA 文件路径列表。
        work_dir: 工作目录，结果文件将写入此目录。

    Returns:
        list[Path]: 生成的各物种 TSV 文件路径列表。

    Raises:
        ValueError: 未能为任何序列获取到 CRISPR 靶点时抛出。
    """
    species_rows: dict[str, list[dict]] = {}

    for fasta_file in fasta_files:
        for seq_name, sequence in _parse_fasta(fasta_file):
            species, gene, accession = _parse_fasta_header(seq_name)
            try:
                all_rows = _extract_rows(_fetch_result_page(sequence, species))
            except Exception as exc:
                logger.warning("CRISPR target design failed for %s: %s", seq_name, exc)
                continue
            if not all_rows:
                logger.warning("Sequence %s returned no CRISPR targets", seq_name)
                continue

            recommended_idx = next(
                (i for i, r in enumerate(all_rows) if r["Region"].strip().lower() == "exon"), None
            )
            sliced = _slice_to_first_recommended(all_rows, recommended_idx)

            for i, row in enumerate(sliced):
                is_recommended = "Yes" if recommended_idx is not None and i == recommended_idx else "No"
                species_rows.setdefault(species, []).append(
                    {
                        "Species": species,
                        "Gene": gene,
                        "Accession": accession,
                        "Seq_name": seq_name,
                        **row,
                        "Recommended": is_recommended,
                    }
                )

    if not species_rows:
        raise ValueError("未能为任何序列获取 CRISPR 靶点")

    output_files = []
    for species, rows in species_rows.items():
        target_file = work_dir / f"{species.replace(' ', '_')}_crispr_target.tsv"
        _write_table(target_file, rows)
        output_files.append(target_file)

    return output_files


__all__ = ["run_crispr_target"]
