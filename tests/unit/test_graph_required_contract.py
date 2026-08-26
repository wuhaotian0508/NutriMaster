from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _settings(tmp_path: Path):
    from nutrimaster.config.settings import RagSettings, Settings

    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    return Settings(
        project_root=tmp_path,
        rag=RagSettings(
            data_dir=data_dir,
            index_dir=index_dir,
            personal_lib_dir=tmp_path / "personal",
        ),
    )


def test_required_graph_fails_closed_when_disabled_or_missing(tmp_path: Path, monkeypatch):
    from nutrimaster.web.deps import _create_graph_source

    settings = _settings(tmp_path)
    monkeypatch.setenv("NUTRIMASTER_REQUIRE_GRAPH_INDEX", "1")
    monkeypatch.setenv("NUTRIMASTER_WEB_BUILD_GRAPH", "0")

    monkeypatch.setenv("NUTRIMASTER_GRAPH_BACKEND", "off")
    with pytest.raises(RuntimeError, match="cannot be disabled"):
        _create_graph_source(settings)

    monkeypatch.setenv("NUTRIMASTER_GRAPH_BACKEND", "sqlite")
    with pytest.raises(RuntimeError, match="missing or invalid"):
        _create_graph_source(settings)


def test_required_graph_accepts_a_populated_read_only_sqlite_index(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.graph import GraphDbSource
    from nutrimaster.web.deps import _create_graph_source

    settings = _settings(tmp_path)
    graph_path = settings.rag.index_dir / "graph_index.sqlite"
    with sqlite3.connect(graph_path) as connection:
        connection.executescript(
            """
            CREATE TABLE nodes(id TEXT PRIMARY KEY);
            CREATE TABLE edges(id TEXT PRIMARY KEY);
            INSERT INTO nodes VALUES ('node-1');
            INSERT INTO edges VALUES ('edge-1');
            """
        )

    monkeypatch.setenv("NUTRIMASTER_REQUIRE_GRAPH_INDEX", "1")
    monkeypatch.setenv("NUTRIMASTER_WEB_BUILD_GRAPH", "0")
    monkeypatch.setenv("NUTRIMASTER_GRAPH_BACKEND", "sqlite")

    source = _create_graph_source(settings)

    assert isinstance(source, GraphDbSource)
    assert source.index.db_path == graph_path
