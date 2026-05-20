from __future__ import annotations

import logging
from pathlib import Path

from nutrimaster.experiment.gene_transfer.gene2updown import GeneSequenceResult

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates_gene_transfer"

# 属名 -> 模板文件前缀映射
_GENUS_TEMPLATE_MAP = {
    "Glycine": "SOP_Glycine_OverExpression.md",
    "Arabidopsis": "SOP_Arabidopsis_OverExpression.md",
    "Nicotiana": "SOP_Nicotiana_OverExpression.md",
    "Oryza": "SOP_Oryza_OverExpression.md",
    "Solanum": "SOP_Solanum_OverExpression.md",
    "Triticum": "SOP_Triticum_OverExpression.md",
    "Zea": "SOP_Zea_OverExpression.md",
}
_FALLBACK_TEMPLATE = "SOP_Universal_Plant_OverExpression.md"

# 单基因 Step 2 标题
_SINGLE_STEP2_TITLE = "## Step 2：单基因表达载体构建（常用于单基因表达验证）"
# 多基因 Step 2 标题
_MULTI_STEP2_TITLE = "## Step 2：GoldenBraid2.0多基因表达载体构建（常用于代谢工程研究）"

# 转基因 Step 1 序列块模板（每个基因一份）——必须与模板文件中的占位符完全一致
_SEQ_BLOCK_TEMPLATE = """>_gene_name___gene_accession_
_gene_sequence_
>_gene_name___gene_accession_up2k
_gene_sequence_up2k_
>_gene_name___gene_accession_down2k
_gene_sequence_down2k_"""

# 多基因 oligo 设计块模板（每个基因一份）
_OLIGO_BLOCK_TEMPLATE = """_gene_name_
正向oligo：GCGCCGTCTCGCTCGAATG + [_gene_up25_]（22 ~25 nt 目标基因上游序列，去掉ATG）
反向oligo：GCGCCGTCTCGCTCAAAGC+ [_gene_down25_rc_]（22 ~25 nt 目标基因下游反向互补序列）"""


def _reverse_complement(seq: str) -> str:
    comp = str.maketrans("ACGTacgt", "TGCAtgca")
    return seq.translate(comp)[::-1]


def _get_template_text(species: str) -> str:
    """根据物种名获取对应属的模板文本，未找到时使用通用模板。"""
    genus = species.strip().split()[0] if species.strip() else ""
    template_file = _TEMPLATES_DIR / _GENUS_TEMPLATE_MAP.get(genus, _FALLBACK_TEMPLATE)
    if not template_file.exists():
        template_file = _TEMPLATES_DIR / _FALLBACK_TEMPLATE
    return template_file.read_text(encoding="utf-8")


def _find_section_range(text: str, heading: str) -> tuple[int, int] | None:
    """找到 heading 所在的段落范围，返回 (start, end) 行索引（包含 end）。

    段落结束于下一个同级（## ）标题或文档末尾。
    """
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading.strip():
            start = i
            break
    if start is None:
        return None

    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## ") and lines[i].strip() != heading.strip():
            return (start, i - 1)
    return (start, len(lines) - 1)


def _remove_section(text: str, heading: str) -> str:
    """从 text 中删除 heading 开头的整个段落（到下一个 ## 标题之前）。"""
    rng = _find_section_range(text, heading)
    if rng is None:
        return text
    lines = text.splitlines(keepends=True)
    del lines[rng[0]:rng[1] + 1]
    return "".join(lines)


def _build_seq_block(result: GeneSequenceResult) -> str:
    """为单个基因构建 Step 1 序列块。"""
    block = _SEQ_BLOCK_TEMPLATE
    # 替换序列占位符（up2k/down2k 需先于 gene_sequence 替换，避免子串误匹配）
    block = block.replace("_gene_sequence_up2k_", result.upstream_seq)
    block = block.replace("_gene_sequence_down2k_", result.downstream_seq)
    block = block.replace("_gene_sequence_", result.gene_seq)
    # accession 带后缀的占位符需先替换，再替换基础 _gene_accession_
    block = block.replace("_gene_accession_up2k", result.accession + "_up2k")
    block = block.replace("_gene_accession_down2k", result.accession + "_down2k")
    block = block.replace("_gene_accession_", result.accession)
    block = block.replace("_gene_name_", result.gene)
    return block


def _build_oligo_block(result: GeneSequenceResult) -> str:
    """为单个基因构建多基因 oligo 设计块。"""
    up25 = result.upstream_seq[-25:] if len(result.upstream_seq) >= 25 else result.upstream_seq
    down25_rc = _reverse_complement(result.downstream_seq[:25]) if result.downstream_seq else ""
    return (
        _OLIGO_BLOCK_TEMPLATE
        .replace("_gene_name_", result.gene)
        .replace("_gene_up25_", up25)
        .replace("_gene_down25_rc_", down25_rc)
    )


def run_gene_transfer_design(
    gene_results: list[GeneSequenceResult],
    species_list: list[str],
) -> dict[str, str]:
    """根据基因序列结果和目标物种列表，生成转基因实验方案 SOP。

    每个物种（按属名查找模板）生成一份 Markdown SOP，填入所有基因的序列信息。
    当只有单个基因时使用单基因 Step 2，多个基因时使用多基因 Step 2。

    Args:
        gene_results: 由 run_gene_transfer_sequences() 返回的基因序列结果列表。
        species_list: 目标物种拉丁名列表，用于匹配模板。

    Returns:
        dict[str, str]: 以物种名为键、Markdown SOP 文本为值的字典。
    """
    sops: dict[str, str] = {}
    is_multi = len(gene_results) > 1

    for species in species_list:
        template = _get_template_text(species)

        # ---- 替换 Step 1 序列块（累加所有基因） ----
        combined_seq_block = "\n".join(_build_seq_block(r) for r in gene_results)
        template = template.replace(_SEQ_BLOCK_TEMPLATE, combined_seq_block)

        # ---- 根据基因数量删除不需要的 Step 2 ----
        if is_multi:
            template = _remove_section(template, _SINGLE_STEP2_TITLE)
            # 替换多基因 oligo 块（单个 _OLIGO_BLOCK_TEMPLATE 占位 -> 累加多行）
            combined_oligo_block = "\n".join(_build_oligo_block(r) for r in gene_results)
            template = template.replace(_OLIGO_BLOCK_TEMPLATE, combined_oligo_block)
        else:
            template = _remove_section(template, _MULTI_STEP2_TITLE)
            # 单基因时仍然填充 oligo 块
            template = template.replace(_OLIGO_BLOCK_TEMPLATE, _build_oligo_block(gene_results[0]))

        sops[species] = template

    return sops
