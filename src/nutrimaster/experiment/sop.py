from __future__ import annotations

from nutrimaster.experiment.resource_limits import (
    ExperimentResourceLimitError,
    MAX_CUMULATIVE_SOP_CHARS,
    SOPOutputBudget,
)


def format_sops(sops: dict[str, str]) -> str:
    """将 SOP 字典格式化为 Markdown 文本。

    将每个物种及其对应的 SOP 内容格式化为二级标题加正文的形式，
    各 SOP 之间以双换行符分隔。

    Args:
        sops: 以物种名为键、SOP 文本内容为值的字典。

    Returns:
        str: 格式化后的 Markdown 文本，每个 SOP 以 "## {species}" 为标题。
    """
    budget = SOPOutputBudget()
    parts: list[str] = []
    formatting_chars = 0
    for index, (species, sop) in enumerate(sops.items()):
        budget.consume(sop, label=str(species))
        heading = f"## {species}\n"
        separator = "\n\n" if index else ""
        formatting_chars += len(separator) + len(heading)
        if formatting_chars > MAX_CUMULATIVE_SOP_CHARS - budget.used:
            raise ExperimentResourceLimitError(
                "formatted SOP output exceeds the hard cumulative limit of "
                f"{MAX_CUMULATIVE_SOP_CHARS} characters"
            )
        parts.extend((separator, heading, sop))
    return "".join(parts)
