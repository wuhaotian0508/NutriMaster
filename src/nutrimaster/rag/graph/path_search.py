from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nutrimaster.rag.graph.extract import GraphQuery, extract_graph_query
from nutrimaster.rag.graph.resolver import Neo4jNodeResolver, ResolvedGraphQuery, ResolvedNode
from nutrimaster.rag.graph.schema import MECHANISM_RELATION_TYPES


@dataclass(frozen=True)
class GraphPathNode:
    """路径中的一个节点。"""

    id: str
    name: str
    type: str
    species: str = ""
    labels: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化字典。"""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "species": self.species,
            "labels": list(self.labels),
        }


@dataclass(frozen=True)
class GraphPathRelationship:
    """路径中的一条有向关系及其证据。"""

    id: str
    source_id: str
    target_id: str
    type: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化字典。"""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "type": self.type,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class GraphPath:
    """一条可传给 agent 的图路径。"""

    nodes: tuple[GraphPathNode, ...]
    relationships: tuple[GraphPathRelationship, ...]
    score: float
    search_kind: str

    def to_dict(self) -> dict[str, Any]:
        """返回可放入 EvidenceItem.metadata 的路径字典。"""
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "relationships": [rel.to_dict() for rel in self.relationships],
            "score": self.score,
            "search_kind": self.search_kind,
        }


@dataclass(frozen=True)
class GraphPathSearchResult:
    """Neo4j 图检索结果。"""

    graph_query: GraphQuery
    starts: tuple[ResolvedNode, ...]
    targets: tuple[ResolvedNode, ...]
    paths: tuple[GraphPath, ...]

    def to_dict(self) -> dict[str, Any]:
        """返回完整 metadata 字典。"""
        return {
            "query": self.graph_query.raw_query,
            "entities": list(self.graph_query.entities),
            "target_entities": list(self.graph_query.target_entities),
            "direction": self.graph_query.direction,
            "species": self.graph_query.species,
            "requested_fields": list(self.graph_query.requested_fields),
            "max_hops": self.graph_query.max_hops,
            "starts": [node.to_dict() for node in self.starts],
            "targets": [node.to_dict() for node in self.targets],
            "paths": [path.to_dict() for path in self.paths],
        }


class Neo4jPathSearcher:
    """执行受限 Cypher 路径搜索。

    这个类不使用任意 shortestPath，而是只走白名单机制关系，并限制 hops。
    原因是生物图里 Species、通用代谢物、通路名可能成为 hub，裸最短路径容易短但无意义。
    """

    def __init__(self, store: Any, resolver: Neo4jNodeResolver | None = None):
        """初始化路径搜索器。

        Args:
            store: Neo4jGraphStore，要求提供 session() 方法。
            resolver: 可选节点解析器；不传则默认创建 Neo4jNodeResolver。
        """
        self.store = store
        self.resolver = resolver or Neo4jNodeResolver(store)

    def search(self, query: str, *, top_k: int = 4, mode: str = "normal", focus: str = "general") -> GraphPathSearchResult:
        """从用户问题检索路径或局部邻域。

        Args:
            query: 用户问题。
            top_k: 最多返回多少条路径。
            mode: rag_search 模式。
            focus: rag_search focus。

        Returns:
            GraphPathSearchResult。没有解析到节点或路径时 paths 为空。
        """
        graph_query = extract_graph_query(query, mode=mode, focus=focus)
        resolved = self.resolver.resolve_graph_query(graph_query, limit=max(4, top_k * 2))
        if not resolved.starts and not resolved.targets:
            return GraphPathSearchResult(graph_query, (), (), ())

        if resolved.targets and resolved.starts and not _same_node_set(resolved.starts, resolved.targets):
            paths = self._paths_between(resolved, limit=top_k)
        else:
            seeds = resolved.targets or resolved.starts
            paths = self._neighborhood(resolved.graph_query, seeds=seeds, limit=top_k)
        return GraphPathSearchResult(
            graph_query=graph_query,
            starts=resolved.starts,
            targets=resolved.targets,
            paths=tuple(paths),
        )

    def _paths_between(self, resolved: ResolvedGraphQuery, *, limit: int) -> list[GraphPath]:
        hops = _clamp_hops(resolved.graph_query.max_hops)
        rel_pattern = _relation_pattern(hops)
        query = f"""
        /* start-target 路径搜索：
           1. 只走机制关系，不走 TESTED_IN/Species hub；
           2. 限制 1..{hops} 跳；
           3. 按路径长度排序，优先给 agent 更短、更可解释的证据链。 */
        MATCH path = (start:GraphNode)-[{rel_pattern}]-(target:GraphNode)
        WHERE start.id IN $start_ids
          AND target.id IN $target_ids
          AND start.id <> target.id
          AND NONE(n IN nodes(path)[1..-1] WHERE coalesce(n.type, '') = 'Species')
        RETURN path, length(path) AS path_length
        ORDER BY path_length ASC
        LIMIT $limit
        """
        with self.store.session() as session:
            rows = session.run(
                query,
                start_ids=[node.id for node in resolved.starts],
                target_ids=[node.id for node in resolved.targets],
                limit=limit,
            ).data()
        return [_path_from_neo4j(row["path"], search_kind="between") for row in rows]

    def _neighborhood(self, graph_query: GraphQuery, *, seeds: tuple[ResolvedNode, ...], limit: int) -> list[GraphPath]:
        hops = _clamp_hops(graph_query.max_hops)
        rel_pattern = _relation_pattern(hops)
        seed_ids = [node.id for node in seeds]
        if graph_query.direction == "upstream":
            # upstream：从 seed 反向找调控者/上游信号，例如 “谁调控 PSY1”。
            match = f"MATCH path = (other:GraphNode)-[{rel_pattern}]->(seed:GraphNode)"
        elif graph_query.direction == "downstream":
            # downstream：从 seed 正向找靶基因/过程/产物，例如 “HY5 下游产物”。
            match = f"MATCH path = (seed:GraphNode)-[{rel_pattern}]->(other:GraphNode)"
        else:
            # both：方向不明确时两边都看，但仍然只走机制关系。
            match = f"MATCH path = (seed:GraphNode)-[{rel_pattern}]-(other:GraphNode)"

        query = f"""
        /* 局部邻域搜索：
           只返回 seed 附近 1..{hops} 跳，不把整张图交给 LLM；
           中间节点过滤 Species，避免“同物种”变成无意义捷径。 */
        {match}
        WHERE seed.id IN $seed_ids
          AND seed.id <> other.id
          AND NONE(n IN nodes(path)[1..-1] WHERE coalesce(n.type, '') = 'Species')
        RETURN path, length(path) AS path_length
        ORDER BY path_length ASC
        LIMIT $limit
        """
        with self.store.session() as session:
            rows = session.run(query, seed_ids=seed_ids, limit=limit).data()
        return [_path_from_neo4j(row["path"], search_kind=graph_query.direction) for row in rows]


def _relation_pattern(hops: int) -> str:
    return f":{'|'.join(MECHANISM_RELATION_TYPES)}*1..{hops}"


def _clamp_hops(value: int) -> int:
    return max(1, min(int(value or 1), 4))


def _same_node_set(left: tuple[ResolvedNode, ...], right: tuple[ResolvedNode, ...]) -> bool:
    return bool(left and right and {node.id for node in left} == {node.id for node in right})


def _path_from_neo4j(path: Any, *, search_kind: str) -> GraphPath:
    nodes = tuple(_node_from_neo4j(node) for node in getattr(path, "nodes", []))
    relationships = tuple(_relationship_from_neo4j(rel) for rel in getattr(path, "relationships", []))
    score = _score_path(nodes, relationships)
    return GraphPath(nodes=nodes, relationships=relationships, score=score, search_kind=search_kind)


def _node_from_neo4j(node: Any) -> GraphPathNode:
    props = dict(node)
    return GraphPathNode(
        id=str(props.get("id") or getattr(node, "element_id", "")),
        name=str(props.get("name") or ""),
        type=str(props.get("type") or ""),
        species=str(props.get("species") or ""),
        labels=tuple(getattr(node, "labels", ()) or ()),
    )


def _relationship_from_neo4j(rel: Any) -> GraphPathRelationship:
    props = dict(rel)
    start_node = getattr(rel, "start_node", None)
    end_node = getattr(rel, "end_node", None)
    source_id = str(dict(start_node).get("id") if start_node is not None else props.get("src", ""))
    target_id = str(dict(end_node).get("id") if end_node is not None else props.get("dst", ""))
    evidence = {
        key: value
        for key, value in props.items()
        if key not in {"id"} and value not in (None, "")
    }
    return GraphPathRelationship(
        id=str(props.get("id") or getattr(rel, "element_id", "")),
        source_id=source_id,
        target_id=target_id,
        type=str(getattr(rel, "type", props.get("relation", ""))),
        evidence=evidence,
    )


def _score_path(nodes: tuple[GraphPathNode, ...], relationships: tuple[GraphPathRelationship, ...]) -> float:
    if not relationships:
        return 0.0
    evidence_bits = 0
    for rel in relationships:
        evidence = rel.evidence
        evidence_bits += int(bool(evidence.get("doi") or evidence.get("source_file")))
        evidence_bits += int(bool(evidence.get("summary")))
        evidence_bits += int(bool(evidence.get("validation")))
    evidence_score = evidence_bits / max(1, len(relationships) * 3)
    length_score = 1.0 / (1 + len(relationships))
    node_score = 0.1 if nodes else 0.0
    return round(length_score + evidence_score + node_score, 4)
