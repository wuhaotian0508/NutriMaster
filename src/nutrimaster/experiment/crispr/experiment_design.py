from __future__ import annotations

import csv
import re
from pathlib import Path

from nutrimaster.experiment.crispr.sop_formatter import format_sop_to_markdown
from nutrimaster.experiment.resource_limits import SOPOutputBudget

_KNOWN_ORGANISMS = {"Oryza", "Zea", "Nicotiana", "Triticum", "Glycine", "Arabidopsis"}


def _template_dirs() -> list[Path]:
    package_dir = Path(__file__).parent
    return [package_dir / "templates"]


def _get_template_text(organism: str) -> str:
    organism = organism if organism in _KNOWN_ORGANISMS else "Universal_Plant"
    filename = f"SOP_{organism}_CRISPR_SpCas9_base.txt"
    for template_dir in _template_dirs():
        template_path = template_dir / filename
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"CRISPR SOP template not found: {filename}")


def _read_fasta_sequences(fasta_file: Path) -> dict[str, str]:
    """读取 FASTA 文件，返回以 accession 为键、序列为值的字典。

    header 格式为 Species_Gene_Accession，accession 取下划线分隔的第3字段起的部分。
    """
    sequences: dict[str, str] = {}
    current_accession = None
    current_lines: list[str] = []
    with fasta_file.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if current_accession is not None:
                    sequences[current_accession] = "".join(current_lines)
                parts = line[1:].split("_")
                current_accession = "_".join(parts[3:]) if len(parts) > 3 else line[1:]
                current_lines = []
            else:
                current_lines.append(line)
    if current_accession is not None:
        sequences[current_accession] = "".join(current_lines)
    return sequences


def _read_recommended_rows(tsv_file: Path) -> dict[str, dict]:
    """读取 crispr_target.tsv，返回每个基因的第一个推荐靶点行，以 Gene 为键。"""
    recommended: dict[str, dict] = {}
    with tsv_file.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            gene = row.get("Gene", "")
            if row.get("Recommended", "").strip().lower() == "yes" and gene not in recommended:
                recommended[gene] = row
    return recommended


def _read_tsv_full_text(tsv_file: Path) -> str:
    """读取 TSV 文件全部文本内容。"""
    return tsv_file.read_text(encoding="utf-8")


def _build_gene_block(
    template_block: str,
    gene_name: str,
    gene_accession: str,
    gene_sequence: str,
    target_sequence: str,
    target_sequence_rc: str,
    target_number: str,
    pam: str,
) -> str:
    """将单个基因的占位符替换为真实值，返回替换后的块文本。"""
    block = template_block
    block = block.replace("_gene_name_", gene_name)
    block = block.replace("_gene_accession_", gene_accession)
    block = block.replace("_gene_sequence_", gene_sequence)
    block = block.replace("_target_sequence_rc_", target_sequence_rc)
    block = block.replace("_target_sequence_", target_sequence)
    block = block.replace("_target_number_", target_number)
    block = block.replace("_PAM_", pam)
    return block


def _normalize(s: str) -> str:
    """归一化字符串：合并空白、转小写，用于模糊匹配。"""
    return re.sub(r"\s+", " ", s).strip().lower()


def _extract_template_block(text: str, start_marker: str, end_marker: str) -> str | None:
    """从模板文本中模糊定位并提取两个标记之间（含标记）的原始块。

    归一化后做子串匹配，容忍空格、换行、大小写、少量非关键字差异。
    当 start_marker == end_marker（单行块）时，直接返回该行对应的原始文本。
    找不到任一标记时返回 None。
    """
    norm_text = _normalize(text)
    norm_start = _normalize(start_marker)
    norm_end = _normalize(end_marker)

    si = norm_text.find(norm_start)
    if si == -1:
        return None

    # 单行块：start 和 end 归一化后相同，直接把 start 所在整行取出
    if norm_start == norm_end:
        ei_end = si + len(norm_start)
    else:
        ei = norm_text.find(norm_end, si + len(norm_start))
        if ei == -1:
            return None
        ei_end = ei + len(norm_end)

    # 将归一化位置映射回原始文本的字符位置
    orig_positions: list[int] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in " \t\n\r":
            orig_positions.append(i)
            i += 1
            while i < len(text) and text[i] in " \t\n\r":
                i += 1
        else:
            orig_positions.append(i)
            i += 1

    def norm_to_orig_start(np: int) -> int:
        return orig_positions[np] if np < len(orig_positions) else len(text)

    def norm_to_orig_end(np: int) -> int:
        if np <= 0:
            return 0
        prev = orig_positions[np - 1]
        j = prev
        if text[j] in " \t\n\r":
            while j < len(text) and text[j] in " \t\n\r":
                j += 1
        else:
            j += 1
        return j

    orig_start = norm_to_orig_start(si)
    orig_end = norm_to_orig_end(ei_end)
    return text[orig_start:orig_end]


def _accumulate_multi_gene_replacements(
    text: str,
    genes: list[dict],
    fasta_sequences: dict[str, str],
) -> str:
    """对模板中各多基因累加块执行替换：每个块按基因列表累加后替换原占位块。"""
    # 块1：Step 1 基因序列块
    block1_start = "• _gene_name_ NCBI GenBank登录号 _gene_accession_"
    block1_end = "(3). 记录外显子-内含子结构（Gene页面 → Genomic regions, transcripts, and products）"
    # 块2：oligo 块
    block2_start = "• _gene_name_\n正向oligo: 5'-GCAG-[_target_sequence_]-3'"
    block2_end = "反向oligo: 5'-AAAC-[_target_sequence_rc_]-3'"
    # 块3：引物块
    block3_start = "• _gene_name_\n正向引物：5'-[ggagtgagtacggtgtgc]-[_target_sequence_]-3'"
    block3_end = "反向引物：5'-[gagttggatgctggatgg]-[_target_sequence_rc_]-3'"
    # 块4：株系命名块
    block4_start = "• _gene_name_-_target_number_-T0-3-T1-7"
    block4_end = "• _gene_name_-_target_number_-T0-3-T1-7"
    # 块5：sgRNA确认块
    block5_start = "• _gene_name_"
    block5_end = "  sgRNA长度：20 nt（PAM）[_target_sequence_] （_PAM_）"

    for block_start, block_end in [
        (block1_start, block1_end),
        (block2_start, block2_end),
        (block3_start, block3_end),
        (block4_start, block4_end),
        (block5_start, block5_end),
    ]:
        template_block = _extract_template_block(text, block_start, block_end)
        if template_block is None:
            continue
        accumulated = []
        for gene_row in genes:
            gene_name = gene_row.get("Gene", "")
            gene_accession = gene_row.get("Accession", "")
            gene_sequence = fasta_sequences.get(gene_accession, "")
            target_sequence = gene_row.get("Sequence", "")
            target_sequence_rc = gene_row.get("Sequence_RC", "")
            target_number = gene_row.get("Target_number", "")
            pam = gene_row.get("PAM", "")
            accumulated.append(_build_gene_block(
                template_block,
                gene_name, gene_accession, gene_sequence,
                target_sequence, target_sequence_rc, target_number, pam,
            ))
        text = text.replace(template_block, "\n".join(accumulated), 1)

    return text


def run_experiment_design(
    fasta_files: list[Path],
    target_files: list[Path],
    work_dir: Path,
) -> dict[str, str]:
    """根据各物种 CRISPR 靶点和序列文件生成每物种一份 SOP 文档。

    Args:
        fasta_files: accession2sequence 生成的各物种 FASTA 文件列表。
        target_files: crispr_target 生成的各物种 TSV 文件列表。
        work_dir: 工作目录，SOP 文件写入此目录。

    Returns:
        dict[str, str]: 以物种名为键、Markdown SOP 文本为值的字典。

    Raises:
        ValueError: 未能生成任何实验方案时抛出。
    """
    # 按物种名（下划线形式）建立索引
    fasta_by_species: dict[str, Path] = {f.stem.replace("_sequence", ""): f for f in fasta_files}
    target_by_species: dict[str, Path] = {f.stem.replace("_crispr_target", ""): f for f in target_files}

    sops: dict[str, str] = {}
    sop_budget = SOPOutputBudget()

    for species_key, tsv_file in target_by_species.items():
        species_display = species_key.replace("_", " ")
        genus = species_display.split(" ")[0]
        template_text = _get_template_text(genus)

        recommended_rows = _read_recommended_rows(tsv_file)
        if not recommended_rows:
            continue

        fasta_file = fasta_by_species.get(species_key)
        fasta_sequences = _read_fasta_sequences(fasta_file) if fasta_file else {}

        genes = list(recommended_rows.values())

        text = _accumulate_multi_gene_replacements(template_text, genes, fasta_sequences)

        # 替换 _crispr_target_candidate_ 为 TSV 全文
        tsv_full = _read_tsv_full_text(tsv_file)
        text = text.replace("_crispr_target_candidate_", tsv_full)

        markdown_text = format_sop_to_markdown(text)
        sop_budget.consume(markdown_text, label=species_display)

        out_file = work_dir / f"{species_key}_SOP_CRISPR_SpCas9.md"
        out_file.write_text(markdown_text, encoding="utf-8")
        sops[species_display] = markdown_text

    if not sops:
        raise ValueError("未能生成任何实验方案")
    return sops


__all__ = ["run_experiment_design"]
