from __future__ import annotations

from pathlib import Path


def test_web_app_uses_canonical_stack_instead_of_manual_legacy_tool_wiring():
    root = Path(__file__).resolve().parents[2]
    source = (root / "src" / "nutrimaster" / "web" / "app.py").read_text(encoding="utf-8")
    deps_source = (root / "src" / "nutrimaster" / "web" / "deps.py").read_text(encoding="utf-8")

    assert not (root / "rag" / "web").exists()
    assert "create_services" in source
    assert "build_agent_stack" not in deps_source
    assert "RagSearchTool" in deps_source
    assert "ExperimentDesignTool" in deps_source
    assert "sys.path.insert" not in source
    assert "import core.config" not in source
    assert "from search." not in source
    assert "from web.auth" not in source
    assert "from skills." not in source
    assert "Settings.from_env" in source
    assert "from core.agent import Agent" not in source
    assert "from skills.skill_loader" not in source
    assert "from search.reranker" not in source


def test_web_app_queues_admin_index_work_outside_the_request_process():
    root = Path(__file__).resolve().parents[2]
    source = (root / "src" / "nutrimaster" / "web" / "app.py").read_text(encoding="utf-8")
    deps_source = (root / "src" / "nutrimaster" / "web" / "deps.py").read_text(encoding="utf-8")

    assert "configure_index_refresh(refresh_admin_index)" in source
    assert "services.request_index_build(" in source
    assert "services.refresh_index(data_dir=data_dir, force=force)" not in source
    assert ".build_index(" not in deps_source
    assert "IndexBuildQueue.from_settings" in deps_source
