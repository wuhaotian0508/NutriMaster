from __future__ import annotations

from pathlib import Path


def test_crispr_pipeline_uses_canonical_llm_gateway_without_sys_path_hack():
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "nutrimaster"
        / "experiment"
        / "crispr"
        / "pipeline.py"
    ).read_text(encoding="utf-8")

    assert "from nutrimaster.experiment.llm import call_experiment_llm" in source
    assert "from core.llm_client" not in source
    assert "sys.path.insert" not in source
    assert "rag.skills.crispr_experiment" not in source
