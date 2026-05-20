"""CRISPR experiment design pipeline."""

from __future__ import annotations

from typing import Any, Generator

from nutrimaster.experiment.crispr.experiment_design import run_experiment_design
from nutrimaster.experiment.crispr.pipeline import ExperimentPipeline


def run_crispr_workflow(pipeline, genes: list[dict[str, Any]]) -> dict[str, str]:
    """运行 CRISPR 实验设计工作流，根据基因列表生成标准操作流程（SOP）。"""
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


def stream_crispr_workflow(pipeline, genes: list[dict[str, Any]]) -> Generator[dict, None, None]:
    """流式运行 CRISPR 工作流，逐个 yield pipeline 事件（progress / result / error）。"""
    yield from pipeline.run_all_from_genes(genes)


__all__ = ["ExperimentPipeline", "run_crispr_workflow", "run_experiment_design", "stream_crispr_workflow"]
