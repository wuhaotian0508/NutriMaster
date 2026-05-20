from __future__ import annotations


def format_sops(sops: dict[str, str]) -> str:
    """将 SOP 字典格式化为 Markdown 文本。

    将每个物种及其对应的 SOP 内容格式化为二级标题加正文的形式，
    各 SOP 之间以双换行符分隔。

    Args:
        sops: 以物种名为键、SOP 文本内容为值的字典。

    Returns:
        str: 格式化后的 Markdown 文本，每个 SOP 以 "## {species}" 为标题。
    """
    return "\n\n".join(f"## {species}\n{sop}" for species, sop in sops.items())
