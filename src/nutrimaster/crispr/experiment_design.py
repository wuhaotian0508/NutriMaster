from __future__ import annotations

import csv
from pathlib import Path

from nutrimaster.crispr.sop_formatter import format_sop_to_markdown

_KNOWN_ORGANISMS = {"Oryza", "Zea", "Nicotiana", "Triticum", "Glycine", "Arabidopsis"}


def _template_dirs() -> list[Path]:
    """获取 CRISPR SOP 模板文件的搜索目录列表。

    Returns:
        list[Path]: 包含模板目录路径的列表，当前返回当前包目录下的 templates 子目录。
    """
    package_dir = Path(__file__).parent
    return [package_dir / "templates"]


def _get_template_text(organism: str) -> str:
    """根据物种名称加载对应的 CRISPR SOP 模板文本。

    如果指定的物种不在已知物种列表中，则回退使用通用植物模板（Universal_Plant）。

    Args:
        organism: 物种属名（如 "Oryza"、"Zea" 等）。

    Returns:
        str: 模板文件的完整文本内容。

    Raises:
        FileNotFoundError: 未找到对应的模板文件时抛出。
    """
    organism = organism if organism in _KNOWN_ORGANISMS else "Universal_Plant"
    filename = f"SOP_{organism}_CRISPR_SpCas9_base.txt"
    for template_dir in _template_dirs():
        template_path = template_dir / filename
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"CRISPR SOP template not found: {filename}")


def _gene_info_from_accessions(accession_file: Path | None) -> dict[str, tuple[str, str]]:
    """从 accession 文件中解析基因名称和物种属名信息。

    Args:
        accession_file: accession TSV 文件路径（基因名\\t物种\\taccession），可为 None。

    Returns:
        dict[str, tuple[str, str]]: 以 accession 为键，值为 (基因名, 物种属名) 的字典。
            物种名仅取第一个单词（属名）。如果文件不存在或为 None，返回空字典。
    """
    info = {}
    if accession_file and accession_file.exists():
        with accession_file.open(encoding="utf-8") as file:
            for line in file:
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    gene, species, accession = parts[:3]
                    info[accession] = (gene, species.split(" ")[0] if species else "Universal_Plant")
    return info


def run_experiment_design(
    target_file: Path,
    work_dir: Path,
    accession_file: Path | None = None,
) -> dict[str, str]:
    """根据 CRISPR 靶点设计结果生成完整的实验方案（SOP）文档。

    读取靶点 TSV 文件，将靶点序列信息填入对应物种的 SOP 模板中，
    生成 Markdown 格式的实验方案，并以两种命名方式写入工作目录。

    Args:
        target_file: CRISPR 靶点推荐结果 TSV 文件路径，包含 Seq_name、Sequence、PAM 等列。
        work_dir: 工作目录，生成的 SOP 文件将写入此目录。
        accession_file: 可选的 accession 信息文件路径，用于匹配基因名和物种。

    Returns:
        dict[str, str]: 以 accession 为键、Markdown 格式 SOP 文本为值的字典。

    Raises:
        ValueError: 未能生成任何实验方案时抛出。
    """
    gene_info = _gene_info_from_accessions(accession_file)
    sops: dict[str, str] = {}
    with target_file.open(encoding="utf-8") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            accession = row["Seq_name"]
            matched_gene, matched_organism = gene_info.get(accession, (accession, "Universal_Plant"))
            text = _get_template_text(matched_organism)
            sequence_rc = row.get("Sequence_RC", "")
            replacements = {
                "_gene_accession_": accession,
                "_target_number_": row.get("Target_number", ""),
                "_sequence_rc_": sequence_rc,
                "_sequence_rt_": sequence_rc,
                "_sequence_": row.get("Sequence", ""),
                "_PAM_": row.get("PAM", ""),
            }
            for placeholder, value in replacements.items():
                text = text.replace(placeholder, value)
            markdown_text = format_sop_to_markdown(text)
            (work_dir / f"SOP_{matched_organism}_CRISPR_SpCas9_{matched_gene}.md").write_text(
                markdown_text,
                encoding="utf-8",
            )
            (work_dir / f"CRISPR_SpCas9_Gene_Editing_{accession}.txt").write_text(
                markdown_text,
                encoding="utf-8",
            )
            sops[accession] = markdown_text
    if not sops:
        raise ValueError("未能生成任何实验方案")
    return sops


__all__ = ["run_experiment_design"]
