from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from nutrimaster.rag.graph.extract import GraphQuery
from nutrimaster.rag.graph.schema import normalize_name


@dataclass(frozen=True)
class ResolvedNode:
    """Neo4j 节点解析结果。

    Attributes:
        id: 图节点稳定 ID。
        name: 节点展示名。
        type: 节点类型，例如 Gene、Metabolite。
        species: Gene 节点的物种信息。
        labels: Neo4j labels。
        score: 当前解析策略给出的匹配分数。
        source: exact/fulltext/fuzzy，表示命中来源。
    """

    id: str
    name: str
    type: str
    species: str = ""
    labels: tuple[str, ...] = ()
    score: float = 0.0
    source: str = "unknown"

    @classmethod
    def from_record(cls, record: dict[str, Any], *, score: float, source: str) -> "ResolvedNode":
        """从 Neo4j 返回记录构造 ResolvedNode。"""
        labels = tuple(record.get("labels") or ())
        return cls(
            id=str(record.get("id") or ""),
            name=str(record.get("name") or ""),
            type=str(record.get("type") or ""),
            species=str(record.get("species") or ""),
            labels=labels,
            score=float(score),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        """返回可放进 EvidenceItem.metadata 的字典。"""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "species": self.species,
            "labels": list(self.labels),
            "score": self.score,
            "source": self.source,
        }


@dataclass(frozen=True)
class ResolvedGraphQuery:
    """GraphQuery 经过节点解析后的结果。"""

    graph_query: GraphQuery
    starts: tuple[ResolvedNode, ...]
    targets: tuple[ResolvedNode, ...] = ()


class Neo4jNodeResolver:
    """把用户问题中的实体名解析成 Neo4j 节点。

    解析顺序是 exact match -> fulltext index -> fuzzy contains。这样短基因名能尽量精确，
    长代谢物/通路名又能靠全文索引召回。
    """

    def __init__(self, store: Any):
        """初始化解析器。

        Args:
            store: Neo4jGraphStore，要求提供 session() 方法。
        """
        self.store = store

    def resolve_graph_query(self, graph_query: GraphQuery, *, limit: int = 6) -> ResolvedGraphQuery:
        """解析 GraphQuery 中的 start 和 target 实体。

        Args:
            graph_query: extract_graph_query 的输出。
            limit: 每组实体最多返回多少候选节点。

        Returns:
            ResolvedGraphQuery。targets 为空时，后续会做邻域搜索。
        """
        starts = self.resolve_entities(graph_query.entities, species=graph_query.species, limit=limit)
        targets = self.resolve_entities(graph_query.target_entities, species=graph_query.species, limit=limit)
        return ResolvedGraphQuery(graph_query=graph_query, starts=tuple(starts), targets=tuple(targets))

    def resolve_entities(self, entities: tuple[str, ...] | list[str], *, species: str = "", limit: int = 6) -> list[ResolvedNode]:
        """把实体字符串列表解析成节点候选。

        Args:
            entities: 用户问题里的实体名。
            species: 可选物种过滤。
            limit: 总候选上限。

        Returns:
            去重后的节点候选，按匹配质量排序。
        """
        output: list[ResolvedNode] = []
        seen: set[str] = set()
        for entity in entities:
            for candidate in self._resolve_one(entity, species=species, limit=limit):
                if candidate.id in seen:
                    continue
                seen.add(candidate.id)
                output.append(candidate)
                if len(output) >= limit:
                    return output
        return output

    def _resolve_one(self, entity: str, *, species: str, limit: int) -> list[ResolvedNode]:
        normalized = normalize_name(entity)
        if not normalized:
            return []

        exact = self._exact_match(normalized, species=species, limit=limit)
        if exact:
            return exact

        fulltext = self._fulltext_match(entity, species=species, limit=limit)
        if fulltext:
            return fulltext

        return self._fuzzy_match(normalized, species=species, limit=limit)

    def _exact_match(self, normalized: str, *, species: str, limit: int) -> list[ResolvedNode]:
        query = f"""
        MATCH (n:GraphNode)
        WHERE n.norm_name = $name
          AND {self._species_filter()}
        RETURN n.id AS id, n.name AS name, n.type AS type, n.species AS species, labels(n) AS labels
        ORDER BY CASE n.type WHEN 'Gene' THEN 0 WHEN 'Metabolite' THEN 1 ELSE 2 END, n.name
        LIMIT $limit
        """
        with self.store.session() as session:
            rows = session.run(query, name=normalized, species=normalize_name(species), limit=limit).data()
        return [ResolvedNode.from_record(row, score=1.0, source="exact") for row in rows]

    def _fulltext_match(self, entity: str, *, species: str, limit: int) -> list[ResolvedNode]:
        query_text = _escape_lucene(entity)
        query = f"""
        CALL db.index.fulltext.queryNodes('graph_nodes_text', $text) YIELD node, score
        WHERE {self._species_filter('node')}
        RETURN node.id AS id,
               node.name AS name,
               node.type AS type,
               node.species AS species,
               labels(node) AS labels,
               score
        ORDER BY score DESC
        LIMIT $limit
        """
        try:
            with self.store.session() as session:
                rows = session.run(query, text=query_text, species=normalize_name(species), limit=limit).data()
        except MemoryError:
            raise
        except Exception:
            return []
        return [
            ResolvedNode.from_record(row, score=min(0.95, 0.65 + float(row.get("score") or 0) / 100.0), source="fulltext")
            for row in rows
        ]

    def _fuzzy_match(self, normalized: str, *, species: str, limit: int) -> list[ResolvedNode]:
        query = f"""
        MATCH (n:GraphNode)
        WHERE (n.norm_name CONTAINS $name OR $name CONTAINS n.norm_name)
          AND {self._species_filter()}
        RETURN n.id AS id, n.name AS name, n.type AS type, n.species AS species, labels(n) AS labels
        ORDER BY size(n.norm_name), n.name
        LIMIT $limit
        """
        with self.store.session() as session:
            rows = session.run(query, name=normalized, species=normalize_name(species), limit=limit).data()
        return [ResolvedNode.from_record(row, score=0.5, source="fuzzy") for row in rows]

    @staticmethod
    def _species_filter(alias: str = "n") -> str:
        """生成 species 过滤片段。

        物种为空时不过滤；节点没有 species 时保留，因为 Metabolite/Pathway 通常不带物种。
        """
        return (
            "$species = '' "
            f"OR coalesce({alias}.species, '') = '' "
            f"OR toLower({alias}.species) CONTAINS $species "
            f"OR $species CONTAINS toLower({alias}.species)"
        )


def _escape_lucene(value: str) -> str:
    """转义 Neo4j fulltext 查询中的 Lucene 特殊字符。"""
    escaped = re.sub(r"([+\-&|!(){}\[\]^\"~*?:\\/])", r"\\\1", value.strip())
    return escaped or value
