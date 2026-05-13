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
_OUTPUT_COLUMNS = ["Seq_name", "On_score", "Target_number", "Sequence", "PAM", "Region", "%GC", "Sequence_RC"]


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


def _fetch_result_page(sequence: str) -> str:
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


def _find_first_exon_row(rows: list[dict[str, str]]) -> dict[str, str] | None:
    """从靶点列表中查找第一个位于外显子（exon）区域的靶点。

    Args:
        rows: CRISPR 靶点信息字典列表。

    Returns:
        dict[str, str] | None: 第一个 Region 为 "exon" 的靶点字典；若无匹配则返回 None。
    """
    return next((row for row in rows if row["Region"].strip().lower() == "exon"), None)


def _write_table(path: Path, rows: list[dict[str, str]], sep: str) -> None:
    """将靶点数据写入分隔符格式的文本文件。

    Args:
        path: 输出文件路径。
        rows: 靶点信息字典列表，每个字典的键应与 _OUTPUT_COLUMNS 对应。
        sep: 列分隔符（如 "\\t" 表示 TSV 格式）。
    """
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=_OUTPUT_COLUMNS, delimiter=sep)
        writer.writeheader()
        writer.writerows(rows)


def run_crispr_target(fasta_file: Path, work_dir: Path) -> Path:
    """批量对 FASTA 文件中的序列进行 CRISPR 靶点设计，筛选外显子区域的最佳靶点。

    对每条序列调用 CRISPR-P 在线平台进行分析，选取第一个位于外显子的靶点，
    将所有结果汇总写入 TSV 文件。

    Args:
        fasta_file: 输入的 FASTA 格式序列文件路径。
        work_dir: 工作目录，结果文件将写入此目录。

    Returns:
        Path: 生成的推荐靶点 TSV 文件路径（work_dir/crispr_target_recommended.txt）。

    Raises:
        ValueError: 未能为任何序列获取到 CRISPR 靶点时抛出。
    """
    target_file = work_dir / "crispr_target_recommended.txt"
    output_rows = []
    for seq_name, sequence in _parse_fasta(fasta_file):
        try:
            exon_row = _find_first_exon_row(_extract_rows(_fetch_result_page(sequence)))
            if exon_row is None:
                logger.warning("Sequence %s has no exon CRISPR target", seq_name)
                continue
            output_rows.append({"Seq_name": seq_name, **exon_row})
        except Exception as exc:
            logger.warning("CRISPR target design failed for %s: %s", seq_name, exc)
    if not output_rows:
        raise ValueError("未能为任何序列获取 CRISPR 靶点")
    _write_table(target_file, output_rows, "\t")
    return target_file


__all__ = ["run_crispr_target"]
