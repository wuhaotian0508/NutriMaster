from __future__ import annotations

from typing import Any


def run_crispr_workflow(pipeline, genes: list[dict[str, Any]]) -> dict[str, str]:
    """运行 CRISPR 实验设计工作流，根据基因列表生成标准操作流程（SOP）。

    遍历 pipeline 产生的所有事件，收集结果类型事件中的 SOP 数据，
    如果遇到错误事件则汇总错误信息并抛出异常。

    Args:
        pipeline: 实验流程管线对象，需提供 run_all_from_genes 方法，
            该方法接收基因列表并以事件流形式返回处理结果。
        genes: 基因信息字典列表，每个字典包含基因名称、物种等字段。

    Returns:
        dict[str, str]: 以登录号（accession）为键、SOP 文本为值的字典。

    Raises:
        RuntimeError: 当工作流中出现一个或多个错误事件时抛出，
            错误信息以换行符拼接。
    """
    sops: dict[str, str] = {}
    errors: list[str] = []
    for event in pipeline.run_all_from_genes(genes):
        if event.get("type") == "result":
            sops = event.get("sops", {})
        elif event.get("type") == "error":
            errors.append(event.get("msg") or event.get("data") or "实验流程失败")
    if errors:
        raise RuntimeError("\n".join(errors))
    return sops
