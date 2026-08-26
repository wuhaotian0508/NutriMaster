from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


# 抽取结果里常见的空值占位符。建图前统一过滤，避免把 "NA" 也建成节点。
INVALID_VALUES = {
    "",
    "-",
    "na",
    "n/a",
    "none",
    "null",
    "nan",
    "unknown",
    "not established",
    "not available",
    "not applicable",
}


# Neo4j 中的核心节点类型。GraphNode 是所有节点的公共 label，下面这些是业务 label。
NODE_LABELS = {
    "Gene",
    "Metabolite",
    "Reaction",
    "Pathway",
    "Process",
    "Signal",
    "Species",
    "Phenotype",
}


# 机制推理时允许走的核心关系。Species/tested_in 等高频关系不放进这里，避免路径被 hub 节点污染。
MECHANISM_RELATION_TYPES = (
    "INPUT_OF",
    "CATALYZES",
    "PRODUCES",
    "PARTICIPATES_IN",
    "CONTRIBUTES_TO",
    "REGULATES",
    "UPSTREAM_SIGNAL_OF",
    "CONTROLS",
    "AFFECTS",
)


# 建图时允许写入的辅助关系。它们可以用于浏览，但默认不参与机制最短路径。
AUXILIARY_RELATION_TYPES = (
    "TESTED_IN",
    "ASSOCIATED_WITH",
    "HAS_EFFECT",
)


RELATION_TYPES = (*MECHANISM_RELATION_TYPES, *AUXILIARY_RELATION_TYPES)


PATHWAY_GRAPH_FIELDS = (
    "Gene_Name",
    "Gene_Accession_Number",
    "Applied_Species",
    "Applied_Species_Latin_Name",
    "Source_Species",
    "Source_Species_Latin_Name",
    "Primary_Substrate",
    "Primary_Product",
    "Catalyzed_Reaction_Description",
    "Biosynthetic_Pathway",
    "Pathway_Branch_or_Subpathway",
    "Metabolic_Step_Position",
    "Terminal_Metabolite",
    "Terminal_Metabolite_Class",
    "Core_Phenotypic_Effect",
    "Core_Validation_Method",
    "Summary_Key_Findings_of_Core_Gene",
)


REGULATION_GRAPH_FIELDS = (
    "Gene_Name",
    "Gene_Accession_Number",
    "Applied_Species",
    "Applied_Species_Latin_Name",
    "Source_Species",
    "Source_Species_Latin_Name",
    "Regulator_Type",
    "Regulation_Mode",
    "Primary_Regulatory_Targets",
    "Regulatory_Effect_on_Target_Genes",
    "Upstream_Signals_or_Inputs",
    "Metabolic_Process_Controlled",
    "Decisive_Influence_on_Target_Product",
    "Terminal_Metabolite",
    "Core_Validation_Method",
    "Summary_Key_Findings_of_Core_Gene",
)


COMMON_GRAPH_FIELDS = (
    "Gene_Name",
    "Gene_Accession_Number",
    "Applied_Species",
    "Applied_Species_Latin_Name",
    "Source_Species",
    "Source_Species_Latin_Name",
    "Terminal_Metabolite",
    "Core_Phenotypic_Effect",
    "Core_Validation_Method",
    "Summary_Key_Findings_of_Core_Gene",
)


GRAPH_FIELD_OPTIONS = {
    "Pathway_Genes": PATHWAY_GRAPH_FIELDS,
    "Regulation_Genes": REGULATION_GRAPH_FIELDS,
    "Common_Genes": COMMON_GRAPH_FIELDS,
}


def default_schema_path() -> Path:
    """返回项目内 nutri_gene_schema_v5.json 的默认路径。

    Returns:
        schema 文件路径。调用方可以用它加载字段说明，也可以传入自己的 schema 路径。
    """
    return Path(__file__).resolve().parents[2] / "extraction" / "prompts" / "nutri_gene_schema_v5.json"


def clean_value(value: Any) -> str:
    """清洗抽取字段，把 NA/unknown 等占位值当作空字符串。

    Args:
        value: JSON 抽取字段中的任意值。

    Returns:
        可用于建图/展示的字符串；如果字段无效则返回空字符串。
    """
    text = str(value or "").strip()
    return "" if text.lower() in INVALID_VALUES else text


def normalize_name(value: Any) -> str:
    """把实体名标准化成可匹配的 key。

    Args:
        value: 原始实体名。

    Returns:
        小写、压缩空白后的名称，用于 exact match 和稳定 ID。
    """
    return re.sub(r"\s+", " ", clean_value(value)).lower()


def split_items(value: Any) -> list[str]:
    """拆分字段里用分号、逗号或换行串起来的多个实体。

    Args:
        value: 例如 "CHS; DFR; ANS" 或列表形式的字段值。

    Returns:
        清洗后的实体名列表。
    """
    if not clean_value(value):
        return []
    raw_items = value if isinstance(value, list) else re.split(r"[;,\n]", str(value))
    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = clean_value(item)
        key = normalize_name(text)
        if not text or key in seen:
            continue
        seen.add(key)
        items.append(text)
    return items


def gene_species(gene: dict[str, Any]) -> str:
    """为 Gene 节点选择最适合消歧的物种字段。

    Gene 节点用 name + species 去重，因为 PAL、CHS、DFR 这类短名会跨物种重复。

    Args:
        gene: 一个 Common/Pathway/Regulation gene 记录。

    Returns:
        优先使用拉丁名，其次使用普通物种名；没有则返回空字符串。
    """
    return (
        clean_value(gene.get("Species_Latin_Name"))
        or clean_value(gene.get("Source_Species_Latin_Name"))
        or clean_value(gene.get("Applied_Species_Latin_Name"))
        or clean_value(gene.get("Source_Species"))
        or clean_value(gene.get("Applied_Species"))
        or clean_value(gene.get("Species"))
    )


def load_graph_field_options(schema_path: Path | str | None = None) -> dict[str, tuple[str, ...]]:
    """从 schema 文件读取字段，并只保留图 RAG 相关字段。

    Args:
        schema_path: 可选 schema 路径；为空时使用项目默认路径。

    Returns:
        section 名到字段名 tuple 的映射。字段不存在时会被自动忽略。
    """
    path = Path(schema_path) if schema_path is not None else default_schema_path()
    if not path.exists():
        return GRAPH_FIELD_OPTIONS

    raw = json.loads(path.read_text(encoding="utf-8"))
    available: dict[str, set[str]] = {}
    for schema in raw.values():
        for name, definition in (schema.get("$defs") or {}).items():
            fields = set((definition.get("properties") or {}).keys())
            if "PathwayGene" in name:
                available.setdefault("Pathway_Genes", set()).update(fields)
            elif "RegulationGene" in name:
                available.setdefault("Regulation_Genes", set()).update(fields)
            elif "CommonGene" in name:
                available.setdefault("Common_Genes", set()).update(fields)

    filtered: dict[str, tuple[str, ...]] = {}
    for section, fields in GRAPH_FIELD_OPTIONS.items():
        section_available = available.get(section)
        if not section_available:
            filtered[section] = tuple(fields)
            continue
        filtered[section] = tuple(field for field in fields if field in section_available)
    return filtered


def relation_type(value: str) -> str:
    """校验 Neo4j 关系类型，避免把用户输入拼进 Cypher。

    Args:
        value: 期望的关系类型。

    Returns:
        大写关系类型。

    Raises:
        ValueError: 当关系不在白名单中。
    """
    rel = value.upper()
    if rel not in RELATION_TYPES:
        raise ValueError(f"Unsupported graph relation type: {value}")
    return rel


def node_label(value: str) -> str:
    """校验 Neo4j 节点 label，避免动态 Cypher 注入。

    Args:
        value: 期望的节点 label。

    Returns:
        合法 label。

    Raises:
        ValueError: 当 label 不在白名单中。
    """
    if value not in NODE_LABELS:
        raise ValueError(f"Unsupported graph node label: {value}")
    return value


def stable_node_id(node_type: str, name: str, species: str = "") -> str:
    """生成跨 SQLite/Neo4j 一致的节点去重 ID。

    Args:
        node_type: 节点类型，例如 Gene、Metabolite。
        name: 节点展示名。
        species: Gene 节点的物种消歧字段。

    Returns:
        SHA1 字符串。Gene 会包含 species，非 Gene 默认只按 type+name 合并。
    """
    species_key = normalize_name(species) if node_type == "Gene" else ""
    raw = f"{node_type}|{normalize_name(name)}|{species_key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def stable_edge_id(src: str, dst: str, relation: str, evidence: dict[str, Any]) -> str:
    """生成语义级边 ID，保证整体图里同一邻接关系只建一条边。

    Args:
        src: 起点节点 ID。
        dst: 终点节点 ID。
        relation: 关系类型。
        evidence: 兼容旧调用保留的参数；语义去重不依赖单条记录来源。

    Returns:
        SHA1 字符串。只要 src/dst/relation 相同，就视为同一条全局邻接边。
    """
    raw = {
        "src": src,
        "dst": dst,
        "relation": relation_type(relation),
    }
    return hashlib.sha1(json.dumps(raw, sort_keys=True).encode("utf-8")).hexdigest()


def relationship_evidence(
    evidence: dict[str, Any],
    *,
    relation: str,
    field: str,
    item: str = "",
    item_index: int = 0,
) -> dict[str, Any]:
    """给边证据补充全局唯一的关系索引元数据。

    Args:
        evidence: 论文级和记录级证据。
        relation: 关系类型。
        field: 产生这条关系的 schema 字段，例如 Primary_Product。
        item: 字段拆分后的具体值。
        item_index: 同一字段拆分后的序号。

    Returns:
        新 evidence 字典，包含 record_key/relationship_index/relationship_key。
    """
    paper = clean_value(evidence.get("doi")) or Path(clean_value(evidence.get("source_file"))).name
    section = clean_value(evidence.get("section"))
    record_index = evidence.get("record_index")
    rel = relation_type(relation)
    record_key = f"{paper}::{section}[{record_index}]"
    relationship_index = f"{record_key}::{field}[{item_index}]::{rel}"
    relationship_key = hashlib.sha1(
        json.dumps(
            {
                "paper": paper,
                "section": section,
                "record_index": record_index,
                "relation": rel,
                "field": field,
                "item": normalize_name(item),
                "item_index": item_index,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **evidence,
        "record_key": record_key,
        "relationship_index": relationship_index,
        "relationship_key": relationship_key,
        "relationship_field": field,
        "relationship_item": clean_value(item),
        "relationship_item_index": item_index,
    }
