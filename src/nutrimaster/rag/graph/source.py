from __future__ import annotations

from pathlib import Path
from typing import Any

from nutrimaster.rag.evidence import EvidenceItem
from nutrimaster.rag.graph.extract import GraphQuery, extract_graph_query, infer_direction as _infer_direction, infer_hops as _infer_hops
from nutrimaster.rag.graph.index import GraphSearchResult, LocalGraphIndex
from nutrimaster.rag.graph.neo4j_store import Neo4jGraphConfig, Neo4jGraphStore
from nutrimaster.rag.graph.path_search import (
    GraphPath,
    GraphPathNode,
    GraphPathRelationship,
    GraphPathSearchResult,
    Neo4jPathSearcher,
)
from nutrimaster.rag.graph.resolver import ResolvedNode


def infer_direction(query: str) -> str:
    """兼容旧调用：根据问题判断图搜索方向。"""
    return _infer_direction(query)


def infer_hops(mode: str, focus: str, has_target: bool = False) -> int:
    """兼容旧调用：根据模式和 focus 推断 hops。"""
    return _infer_hops(mode=mode, focus=focus, has_target=has_target)


class GraphDbSource:
    """SQLite 图源适配器，让本地图谱像 PubMed/GeneDB 一样接入 RAGSearchService。

    这是轻量 fallback：只需要一个 sqlite 文件，不需要 Neo4j 服务。它返回 seed 附近
    1-2 跳邻域，适合开发环境和 Neo4j 不可用时兜底。
    """

    source_type = "graph_db"

    def __init__(self, db_path: Path | str):
        """初始化 SQLite 图源。

        Args:
            db_path: LocalGraphIndex 的 sqlite 文件路径。
        """
        self.index = LocalGraphIndex(db_path)

    async def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        mode: str = "normal",
        focus: str = "general",
        **_: Any,
    ) -> list[EvidenceItem]:
        """搜索 SQLite 局部邻域并返回 EvidenceItem。"""
        if not self.index.db_path.exists():
            return []

        graph_query = extract_graph_query(query, mode=mode, focus=focus)
        direction = infer_direction(query)
        hops = infer_hops(mode=mode, focus=focus, has_target=bool(graph_query.target_entities))
        graph = self.index.neighborhood(
            query,
            species=graph_query.species,
            hops=hops,
            direction=direction,
            limit=max(30, top_k * 8),
        )
        if not graph.edges:
            return []

        return [
            EvidenceItem(
                source_id="",
                source_type=self.source_type,
                title=f"Graph neighborhood: {query}",
                content=self._render(query, graph),
                score=1.0,
                metadata={"backend": "sqlite", **_sqlite_graph_to_path_result(graph_query, graph).to_dict()},
            )
        ]

    @staticmethod
    def _render(query: str, graph: GraphSearchResult) -> str:
        """把 SQLite 局部子图压缩成 LLM 能读的证据文本。"""
        nodes = {node["id"]: node for node in graph.nodes}
        lines = [
            f"图谱局部邻域: {query}",
            "说明: 这些关系来自本地结构化 JSON，只展示查询主体附近的边。",
        ]

        if graph.seeds:
            seed_text = "; ".join(
                f"{seed['name']} ({seed['type']}" + (f", {seed['species']}" if seed.get("species") else "") + ")"
                for seed in graph.seeds[:8]
            )
            lines.append(f"匹配主体: {seed_text}")

        for edge in graph.edges[:40]:
            src = nodes.get(edge["src"], {})
            dst = nodes.get(edge["dst"], {})
            evidence = edge.get("evidence", {})
            source = evidence.get("doi") or evidence.get("source_file") or ""
            validation = evidence.get("validation") or ""
            summary = evidence.get("summary") or ""
            mode = evidence.get("mode") or ""

            extra = []
            if mode:
                extra.append(f"mode={mode}")
            if validation:
                extra.append(f"validation={validation}")
            if summary:
                extra.append(f"summary={summary}")
            if source:
                extra.append(f"evidence={source}")

            suffix = "; " + "; ".join(extra) if extra else ""
            lines.append(
                f"- {src.get('name', edge['src'])} ({src.get('type', '')}) "
                f"--{edge['relation']}--> "
                f"{dst.get('name', edge['dst'])} ({dst.get('type', '')})"
                f"{suffix}"
            )

        return "\n".join(lines)


class Neo4jGraphSource:
    """Neo4j 图 RAG 证据源。

    它把用户问题解析成 GraphQuery，执行受限 Cypher 路径搜索，然后把 top-k 路径渲染成
    EvidenceItem。Agent 看到的是短路径证据，不会看到整张图。
    """

    source_type = "graph_db"

    def __init__(self, store: Neo4jGraphStore | None = None, *, searcher: Any | None = None):
        """初始化 Neo4j 图源。

        Args:
            store: Neo4jGraphStore。传入 searcher 时可以为空，方便测试。
            searcher: 可选 Neo4jPathSearcher 或测试替身。
        """
        self.store = store
        self.searcher = searcher or Neo4jPathSearcher(store)

    @classmethod
    def from_env(cls) -> "Neo4jGraphSource":
        """从环境变量创建 Neo4jGraphSource。

        Returns:
            配好 Neo4jGraphStore 的 Neo4jGraphSource。
        """
        store = Neo4jGraphStore(Neo4jGraphConfig.from_env())
        return cls(store)

    async def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        mode: str = "normal",
        focus: str = "general",
        **_: Any,
    ) -> list[EvidenceItem]:
        """执行 Neo4j 路径检索并返回 EvidenceItem。

        Neo4j 未启动或不可达时直接返回空列表，由 RAGSearchService 继续使用其他来源。
        """
        if self.store is not None and hasattr(self.store, "is_available") and not self.store.is_available():
            return []

        result = self.searcher.search(query, top_k=top_k, mode=mode, focus=focus)
        if not result.paths:
            return []

        first_doi = _first_evidence_value(result, "doi")
        first_journal = _first_evidence_value(result, "journal")
        return [
            EvidenceItem(
                source_id="",
                source_type=self.source_type,
                title=_neo4j_title(result),
                content=self._render_paths(result),
                doi=first_doi,
                journal=first_journal,
                score=max(path.score for path in result.paths),
                metadata={"backend": "neo4j", **result.to_dict()},
            )
        ]

    @staticmethod
    def _render_paths(result: GraphPathSearchResult) -> str:
        """把 Neo4j 路径渲染成 agent 可读文本。"""
        query = result.graph_query
        lines = [
            f"Neo4j 图路径证据: {query.raw_query}",
            f"方向: {query.direction}; 最大跳数: {query.max_hops}; 物种限定: {query.species or '未限定'}",
            "说明: 这些路径只走机制关系，并过滤 Species 这类 hub 中间节点。",
        ]
        if result.starts:
            lines.append("起点候选: " + "; ".join(_node_label(node.to_dict()) for node in result.starts[:6]))
        if result.targets:
            lines.append("目标候选: " + "; ".join(_node_label(node.to_dict()) for node in result.targets[:6]))

        for index, path in enumerate(result.paths, start=1):
            nodes = {node.id: node for node in path.nodes}
            lines.append(f"路径 {index} ({path.search_kind}, score={path.score:.3f}):")
            for rel in path.relationships:
                src = nodes.get(rel.source_id)
                dst = nodes.get(rel.target_id)
                evidence = rel.evidence
                details = []
                if evidence.get("domain"):
                    details.append(f"domain={evidence['domain']}")
                if evidence.get("mode"):
                    details.append(f"mode={evidence['mode']}")
                if evidence.get("validation"):
                    details.append(f"validation={evidence['validation']}")
                if evidence.get("summary"):
                    details.append(f"summary={evidence['summary']}")
                source = evidence.get("doi") or evidence.get("source_file")
                if source:
                    details.append(f"evidence={source}")
                suffix = "; " + "; ".join(details) if details else ""
                lines.append(
                    f"- {_node_label(src.to_dict() if src else {'id': rel.source_id})} "
                    f"-[:{rel.type}]-> "
                    f"{_node_label(dst.to_dict() if dst else {'id': rel.target_id})}"
                    f"{suffix}"
                )
        return "\n".join(lines)


def _neo4j_title(result: GraphPathSearchResult) -> str:
    starts = ", ".join(node.name for node in result.starts[:2])
    targets = ", ".join(node.name for node in result.targets[:2])
    if starts and targets:
        return f"Graph path: {starts} -> {targets}"
    return f"Graph neighborhood: {result.graph_query.raw_query}"


def _sqlite_graph_to_path_result(graph_query: GraphQuery, graph: GraphSearchResult) -> GraphPathSearchResult:
    """把 SQLite 邻域结果转换成与 Neo4j 一致的路径结果对象。"""
    nodes = {node["id"]: node for node in graph.nodes}
    starts = tuple(
        ResolvedNode(
            id=str(seed.get("id") or ""),
            name=str(seed.get("name") or ""),
            type=str(seed.get("type") or ""),
            species=str(seed.get("species") or ""),
            labels=(str(seed.get("type") or ""),) if seed.get("type") else (),
            score=1.0,
            source="sqlite",
        )
        for seed in graph.seeds
    )
    paths: list[GraphPath] = []
    for edge in graph.edges:
        src = nodes.get(edge.get("src"), {"id": edge.get("src", "")})
        dst = nodes.get(edge.get("dst"), {"id": edge.get("dst", "")})
        relationship = GraphPathRelationship(
            id=str(edge.get("id") or ""),
            source_id=str(edge.get("src") or ""),
            target_id=str(edge.get("dst") or ""),
            type=str(edge.get("relation") or ""),
            evidence=dict(edge.get("evidence") or {}),
        )
        paths.append(
            GraphPath(
                nodes=(_path_node_from_mapping(src), _path_node_from_mapping(dst)),
                relationships=(relationship,),
                score=1.0,
                search_kind=graph_query.direction,
            )
        )
    return GraphPathSearchResult(graph_query=graph_query, starts=starts, targets=(), paths=tuple(paths))


def _path_node_from_mapping(node: dict[str, Any]) -> GraphPathNode:
    node_type = str(node.get("type") or "")
    return GraphPathNode(
        id=str(node.get("id") or ""),
        name=str(node.get("name") or node.get("id") or ""),
        type=node_type,
        species=str(node.get("species") or ""),
        labels=(node_type,) if node_type else (),
    )


def _node_label(node: dict[str, Any]) -> str:
    name = node.get("name") or node.get("id") or ""
    node_type = node.get("type") or ""
    species = node.get("species") or ""
    label = f"{name} ({node_type})" if node_type else str(name)
    return f"{label}, {species}" if species else label


def _first_evidence_value(result: GraphPathSearchResult, key: str) -> str:
    for path in result.paths:
        for rel in path.relationships:
            value = rel.evidence.get(key)
            if value:
                return str(value)
    return ""
