from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from nutrimaster.rag.graph.schema import GRAPH_FIELD_OPTIONS


ENTITY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,48}")
LATIN_SPECIES_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[a-z][a-z.-]+){1,2}\b")
QUOTED_RE = re.compile(r"['\"]([^'\"]{2,80})['\"]")


STOPWORDS = {
    "and",
    "or",
    "the",
    "how",
    "what",
    "which",
    "does",
    "do",
    "in",
    "of",
    "for",
    "to",
    "with",
    "by",
    "gene",
    "genes",
    "pathway",
    "regulation",
    "regulate",
    "regulates",
    "regulated",
    "target",
    "targets",
    "upstream",
    "downstream",
    "product",
    "products",
    "affect",
    "affects",
    "effect",
    "mechanism",
    "neo4j",
    "rag",
}


FIELD_HINTS = {
    "Primary_Substrate": ("底物", "substrate", "precursor", "前体"),
    "Primary_Product": ("产物", "product", "produces", "生成"),
    "Biosynthetic_Pathway": ("通路", "pathway", "biosynthesis", "生物合成"),
    "Terminal_Metabolite": ("代谢物", "metabolite", "lycopene", "anthocyanin", "gaba"),
    "Primary_Regulatory_Targets": ("靶基因", "target", "targets", "调控谁"),
    "Upstream_Signals_or_Inputs": ("上游信号", "signal", "signals", "input", "stress", "light"),
    "Regulation_Mode": ("激活", "抑制", "activation", "repression", "mode"),
    "Metabolic_Process_Controlled": ("过程", "process", "controlled", "控制"),
}


UPSTREAM_HINTS = ("上游", "谁调控", "调控因子", "upstream", "regulator", "precursor")
DOWNSTREAM_HINTS = ("下游", "产物", "生成", "produces", "product", "downstream", "affect", "影响")
BETWEEN_HINTS = ("如何影响", "怎么影响", "relationship", "between", "connect", "path", "路径", "->")


@dataclass(frozen=True)
class GraphQuery:
    """图检索用的结构化查询。

    Attributes:
        raw_query: 用户原始问题。
        entities: 从问题中抽出的候选实体，通常是基因、代谢物、通路名。
        target_entities: 当问题表达“X 如何影响 Y”时，这里保存 Y。
        species: 物种限定；为空表示不限定物种。
        direction: 图跳转方向，取 upstream/downstream/both。
        focus: 用户或 agent 给出的检索重点。
        requested_fields: 问题里暗示的 schema 字段，用于后续解释和过滤。
        max_hops: 建议 Cypher 搜索的最大跳数。
    """

    raw_query: str
    entities: tuple[str, ...]
    target_entities: tuple[str, ...] = ()
    species: str = ""
    direction: str = "both"
    focus: str = "general"
    requested_fields: tuple[str, ...] = ()
    max_hops: int = 2


def extract_graph_query(query: str, *, mode: str = "normal", focus: str = "general") -> GraphQuery:
    """把自然语言问题转成图检索参数。

    这里先使用确定性规则，而不是把 LLM 放进第一版硬依赖。规则的作用不是完美理解问题，
    而是稳定地提供 seed 实体、方向和 target，后面 resolver 会再做节点消歧。

    Args:
        query: 用户原始问题。
        mode: rag_search 的 normal/deep 模式。
        focus: rag_search 的 focus，例如 pathway、mechanism、gene_function。

    Returns:
        GraphQuery，供节点解析和 Cypher 路径搜索使用。
    """
    raw = query.strip()
    species = _extract_species(raw)
    entities = _remove_species_tokens(_dedupe([*_extract_quoted(raw), *_extract_tokens(raw)]), species)
    direction = infer_direction(raw)
    target_entities = _infer_target_entities(raw, entities, direction)
    if target_entities:
        entities = tuple(entity for entity in entities if entity not in target_entities) or entities
    max_hops = infer_hops(mode=mode, focus=focus, has_target=bool(target_entities))

    return GraphQuery(
        raw_query=raw,
        entities=tuple(entities),
        target_entities=tuple(target_entities),
        species=species,
        direction=direction,
        focus=focus,
        requested_fields=tuple(infer_requested_fields(raw)),
        max_hops=max_hops,
    )


def infer_direction(query: str) -> str:
    """根据问题词判断图的跳转方向。

    Args:
        query: 用户问题。

    Returns:
        upstream 表示从结果节点反查调控者，downstream 表示从 seed 往产物/靶点走，both 表示两边都看。
    """
    lower = query.lower()
    has_upstream = any(hint in lower for hint in UPSTREAM_HINTS)
    has_downstream = any(hint in lower for hint in DOWNSTREAM_HINTS)
    if has_upstream and has_downstream:
        return "both"
    if has_upstream:
        return "upstream"
    if has_downstream:
        return "downstream"
    return "both"


def infer_hops(*, mode: str, focus: str, has_target: bool) -> int:
    """根据搜索模式决定路径最大跳数。

    Args:
        mode: normal/deep。
        focus: 检索重点。
        has_target: 是否存在明确 start-target 路径问题。

    Returns:
        建议最大跳数。带 target 的机制问题默认给 4 跳，邻域问题默认 1-2 跳。
    """
    if has_target:
        return 4 if mode == "deep" or focus in {"pathway", "mechanism", "gene_function"} else 3
    return 2 if mode == "deep" or focus in {"pathway", "mechanism", "gene_function"} else 1


def infer_requested_fields(query: str) -> list[str]:
    """从问题词映射到 schema 字段选项。

    Args:
        query: 用户问题。

    Returns:
        与问题相关的有限字段名列表，字段来自 nutri_gene_schema_v5.json 中的图相关子集。
    """
    lower = query.lower()
    allowed = {field for fields in GRAPH_FIELD_OPTIONS.values() for field in fields}
    fields = []
    for field, hints in FIELD_HINTS.items():
        if field in allowed and any(hint.lower() in lower for hint in hints):
            fields.append(field)
    return fields


def _extract_tokens(query: str) -> list[str]:
    tokens = []
    for token in ENTITY_RE.findall(query):
        lowered = token.lower()
        if lowered in STOPWORDS:
            continue
        if "_" in token and token in {field for fields in GRAPH_FIELD_OPTIONS.values() for field in fields}:
            continue
        tokens.append(token)
    return tokens


def _extract_quoted(query: str) -> list[str]:
    return [match.group(1).strip() for match in QUOTED_RE.finditer(query) if match.group(1).strip()]


def _extract_species(query: str) -> str:
    """优先识别拉丁名物种；普通物种名先作为实体处理，由 resolver 再消歧。"""
    matches = [match.group(0) for match in LATIN_SPECIES_RE.finditer(query)]
    return matches[0] if matches else ""


def _infer_target_entities(query: str, entities: tuple[str, ...] | list[str], direction: str) -> tuple[str, ...]:
    if not entities:
        return ()

    lower = query.lower()
    if "->" in query:
        right = query.split("->", 1)[1]
        return tuple(_dedupe(_extract_tokens(right)))[:2]

    if any(hint in lower for hint in BETWEEN_HINTS):
        return (entities[-1],) if len(entities) >= 2 else ()

    # “谁调控 PSY1”这类问题只有一个实体，但它是反向搜索的目标。
    if direction == "upstream":
        return (entities[-1],)

    return ()


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen = set()
    for value in values:
        text = value.strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return tuple(result)


def _remove_species_tokens(entities: tuple[str, ...], species: str) -> tuple[str, ...]:
    if not species:
        return entities
    species_key = species.lower()
    species_parts = set(species_key.split())
    return tuple(
        entity
        for entity in entities
        if entity.lower() != species_key and entity.lower() not in species_parts
    )
