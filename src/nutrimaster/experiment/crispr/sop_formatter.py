from __future__ import annotations

import re

_META_RE = re.compile(r"^(物种|适用物种|系统|转化策略|验证策略|流程覆盖|文件版本)\s*[：:]\s*(.+)$")
_STEP_RE = re.compile(r"^(Step\s+\d+)\s*[：:]\s*(.+)$")
_H3_RE = re.compile(r"^(\d+\.\d+)\s+(.+)$")
_H4_NUM_RE = re.compile(r"^(\d+\.\d+\.\d+)\s+(.+)$")
_H4_LETTER_RE = re.compile(r"^（[A-Z]）(.+)$")
_DAY_RE = re.compile(r"^(Day\s+[\d\-]+\s*(?:至\s*Day\s*\d+)?)\s*[-–—]\s*(.+)$")
_FLOW_CHAR_RE = re.compile(r"[│▼▲【】]")
_HR_RE = re.compile(r"^_{5,}$")
_SUB_BULLET_RE = re.compile(r"^o\t(.+)$")
_CALLOUT_RE = re.compile(r"^(注意|推荐|关键提示|决策节点|转化效率参考|命名规则|判断标准|原则)[：:]\s*(.+)$")
_LABEL_RE = re.compile(
    r"^(试剂|操作|材料|菌株|大豆品种|种子处理|目的|PCR条件|PCR程序|保存|参考方案"
    r"|小麦品种|水稻品种|番茄品种|玉米品种|烟草品种|拟南芥品种|植物品种)[：:]\s*(.*)$"
)
_SECTION_CANDIDATES = {"总体流程概览", "附录", "参考文献", "参考资源"}


def _is_list_item(line: str) -> bool:
    """判断一行文本是否为列表项。

    支持识别以 "•" 开头的无序列表、"o\\t" 开头的子列表项和 "数字.\\t" 开头的有序列表。

    Args:
        line: 待判断的单行文本。

    Returns:
        bool: 如果是列表项返回 True，否则返回 False。
    """
    return bool(line.startswith("•") or re.match(r"^o\t", line) or re.match(r"^\d+\.\t", line))


def _is_table_row(line: str) -> bool:
    """判断一行文本是否为制表符分隔的表格行。

    要求包含制表符且不是列表项，并且按制表符拆分后至少有 2 个非空单元格。

    Args:
        line: 待判断的单行文本。

    Returns:
        bool: 如果是表格行返回 True，否则返回 False。
    """
    if "\t" not in line or _is_list_item(line):
        return False
    return len([part for part in (item.strip() for item in line.split("\t")) if part]) >= 2


def _make_table_row(line: str) -> str:
    """将制表符分隔的行转换为 Markdown 表格行格式。

    Args:
        line: 制表符分隔的文本行。

    Returns:
        str: Markdown 表格行（如 "| 列1 | 列2 | 列3 |"）。
    """
    return "| " + " | ".join(part.strip() or " " for part in line.split("\t")) + " |"


def _make_table_separator(line: str) -> str:
    """根据制表符分隔的行生成 Markdown 表格分隔行。

    Args:
        line: 制表符分隔的文本行，用于确定列数。

    Returns:
        str: Markdown 表格分隔行（如 "| --- | --- | --- |"）。
    """
    return "| " + " | ".join("---" for _ in line.split("\t")) + " |"


def format_sop_to_markdown(text: str) -> str:
    """将 SOP 纯文本格式转换为结构化的 Markdown 文档。

    识别并转换以下元素：
    - 文档标题（首行转为 H1）
    - 元数据行（物种、系统等，转为加粗标签）
    - Step 步骤标题（转为 H2）
    - 数字编号的章节和小节标题（转为 H3/H4）
    - Day 时间标记（转为 H4）
    - 注意/推荐等提示信息（转为 Markdown 引用块）
    - 列表项（无序/有序/子列表）
    - 制表符分隔的表格（转为 Markdown 表格）
    - 流程图字符（用代码块包裹）
    - 水平分隔线

    Args:
        text: SOP 纯文本内容。

    Returns:
        str: 转换后的 Markdown 格式文本。
    """
    lines = text.split("\n")
    output: list[str] = []
    in_flow = False
    in_table = False
    title_done = False
    index = 0
    while index < len(lines):
        stripped = lines[index].rstrip().strip()
        if not stripped:
            if in_flow:
                output.append("```")
                in_flow = False
            in_table = False
            output.append("")
            index += 1
            continue
        if _HR_RE.match(stripped):
            if in_flow:
                output.append("```")
                in_flow = False
            in_table = False
            output.append("---")
            index += 1
            continue
        if not title_done:
            output.append(f"# {stripped}")
            title_done = True
            index += 1
            continue
        if in_flow:
            output.append(stripped)
            index += 1
            continue
        for regex, prefix in (
            (_META_RE, "**{0}：** {1}"),
            (_STEP_RE, "\n## {0}：{1}"),
            (_H4_NUM_RE, "#### {0} {1}"),
            (_H3_RE, "### {0} {1}"),
            (_DAY_RE, "#### {0} — {1}"),
            (_CALLOUT_RE, "> **{0}：** {1}"),
        ):
            match = regex.match(stripped)
            if match:
                in_table = False
                output.append(prefix.format(*match.groups()))
                index += 1
                break
        else:
            if stripped in _SECTION_CANDIDATES or (stripped.startswith("附录") and len(stripped) < 40):
                in_table = False
                output.extend(["", f"## {stripped}"])
            elif _H4_LETTER_RE.match(stripped):
                output.append(f"#### {stripped}")
            elif match := _LABEL_RE.match(stripped):
                rest = match.group(2)
                output.append(f"**{match.group(1)}：** {rest}" if rest else f"**{match.group(1)}：**")
            elif match := _SUB_BULLET_RE.match(stripped):
                output.append(f"  - {match.group(1)}")
            elif stripped.startswith("•"):
                output.append(f"- {stripped.lstrip('•').strip()}")
            elif match := re.match(r"^(\d+)\.\t(.+)$", stripped):
                output.append(f"{match.group(1)}. {match.group(2)}")
            elif _FLOW_CHAR_RE.search(stripped):
                in_table = False
                output.extend(["```", stripped])
                in_flow = True
            elif _is_table_row(stripped):
                if not in_table:
                    output.append(_make_table_row(stripped))
                    output.append(_make_table_separator(stripped))
                    in_table = True
                else:
                    output.append(_make_table_row(stripped))
            else:
                in_table = False
                output.append(stripped)
            index += 1
    if in_flow:
        output.append("```")
    return "\n".join(output)


__all__ = ["format_sop_to_markdown"]
