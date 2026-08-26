from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_INVALID_VALUES = {
    "",
    "-",
    "na",
    "n/a",
    "none",
    "null",
    "nan",
    "unknown",
    "not available",
    "not applicable",
}


def clean_text(value: object) -> str:
    """清洗文本值，去除空白并过滤无效值。

    将输入值转为字符串后去除首尾空白，若结果属于预定义的无效值集合
    （如空字符串、"NA"、"null"、"unknown" 等），则返回空字符串。

    Args:
        value: 任意输入值，将被转换为字符串进行处理。

    Returns:
        清洗后的字符串；若为无效值则返回空字符串。
    """
    text = str(value or "").strip()
    return "" if text.lower() in _INVALID_VALUES else text


def normalize_doi(value: object) -> str:
    """标准化 DOI 标识符。

    去除 DOI 中常见的 URL 前缀（如 https://doi.org/、https://dx.doi.org/）
    和 "doi:" 前缀，返回纯 DOI 字符串。若结果为无效值则返回空字符串。

    Args:
        value: 包含 DOI 的任意值（可能带有 URL 前缀）。

    Returns:
        标准化后的纯 DOI 字符串；若无有效 DOI 则返回空字符串。
    """
    doi = clean_text(value)
    if not doi:
        return ""
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I).strip()
    doi = doi.removeprefix("doi:").strip()
    if doi.lower() in _INVALID_VALUES:
        return ""
    return doi


def normalize_pmid(value: object) -> str:
    """标准化 PubMed ID（PMID）。

    从输入值中提取纯数字部分作为 PMID。

    Args:
        value: 包含 PMID 的任意值。

    Returns:
        提取到的纯数字 PMID 字符串；若无有效数字则返回空字符串。
    """
    pmid = clean_text(value)
    if not pmid:
        return ""
    match = re.search(r"\d+", pmid)
    return match.group(0) if match else ""


def normalize_url(value: object) -> str:
    """标准化 URL 地址。

    验证输入值是否为以 http:// 或 https:// 开头的合法 URL。

    Args:
        value: 包含 URL 的任意值。

    Returns:
        若为合法 URL 则返回清洗后的字符串；否则返回空字符串。
    """
    url = clean_text(value)
    return url if re.match(r"^https?://", url, flags=re.I) else ""


def title_key(value: object) -> str:
    """将标题转换为标准化的去重键。

    将标题转为小写，去除 HTML 标签和非字母数字字符，
    并将连续空白压缩为单个空格，用于标题级别的去重比较。

    Args:
        value: 标题文本。

    Returns:
        标准化后的标题键字符串。
    """
    title = clean_text(value).lower()
    title = re.sub(r"<[^>]+>", " ", title)
    title = re.sub(r"[\W_]+", " ", title, flags=re.UNICODE)
    return re.sub(r"\s+", " ", title).strip()


def evidence_key(*, title: str, doi: str = "", pmid: str = "", url: str = "") -> tuple[str, str]:
    """根据文献标识信息生成唯一去重键。

    按优先级依次尝试 DOI、PMID、URL、标题来生成唯一标识，
    用于文献级别的去重。优先级：DOI > PMID > URL > 标题。

    Args:
        title: 文献标题。
        doi: DOI 标识符（可选）。
        pmid: PubMed ID（可选）。
        url: 文献 URL（可选）。

    Returns:
        二元组 (标识类型, 标识值)，如 ("doi", "10.1038/xxx") 或 ("title", "normalized title")。
    """
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        return ("doi", normalized_doi.lower())
    normalized_pmid = normalize_pmid(pmid)
    if normalized_pmid:
        return ("pmid", normalized_pmid)
    normalized_url = normalize_url(url)
    if normalized_url:
        return ("url", normalized_url.rstrip("/").lower())
    return ("title", title_key(title))


@dataclass(frozen=True)
class EvidenceItem:
    """单条证据项，表示从某个来源检索到的一条文献证据。

    包含文献的基本元信息（标题、DOI、PMID、期刊等）以及内容和评分。
    作为不可变数据类，创建后字段值不可修改（通过 __post_init__ 进行标准化除外）。

    Attributes:
        source_id: 证据来源编号（用于引用标注）。
        source_type: 来源类型（如 "pubmed"、"gene_db"、"personal"）。
        title: 文献标题。
        content: 文献内容或摘要。
        url: 文献链接。
        doi: DOI 标识符。
        pmid: PubMed ID。
        journal: 期刊名称。
        gene_name: 关联的基因名称。
        score: 检索相关性评分。
        metadata: 附加元数据字典。
    """
    source_id: str
    source_type: str
    title: str
    content: str
    url: str = ""
    doi: str = ""
    pmid: str = ""
    journal: str = ""
    gene_name: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """初始化后处理：标准化所有字段值。

        对 DOI、PMID、URL 进行标准化处理，并自动根据 DOI 或 PMID
        生成缺失的 URL。对标题、内容、期刊、基因名称进行文本清洗。
        """
        doi = normalize_doi(self.doi)
        pmid = normalize_pmid(self.pmid)
        url = normalize_url(self.url)
        if not url and doi:
            url = f"https://doi.org/{doi}"
        elif not url and pmid:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        object.__setattr__(self, "title", clean_text(self.title))
        object.__setattr__(self, "content", clean_text(self.content))
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "doi", doi)
        object.__setattr__(self, "pmid", pmid)
        object.__setattr__(self, "journal", clean_text(self.journal))
        object.__setattr__(self, "gene_name", clean_text(self.gene_name))

    def to_citation(self) -> dict[str, Any]:
        """将证据项转换为引用字典格式。

        生成一个包含所有引用信息的字典，适用于前端展示或 JSON 序列化。

        Returns:
            包含 source_id、source_type、title、url、doi、pmid、journal、
            gene_name、score、metadata 等字段的字典。
        """
        return {
            "source_id": self.source_id,
            "tool_index": int(self.source_id) if self.source_id.isdigit() else 0,
            "source_type": self.source_type,
            "title": self.title,
            "paper_title": self.title,
            "url": self.url,
            "doi": self.doi,
            "pmid": self.pmid,
            "journal": self.journal,
            "gene_name": self.gene_name,
            "score": self.score,
            "metadata": self.metadata,
        }

    def to_prompt_block(self) -> str:
        """将证据项格式化为提示词文本块。

        生成适合注入到 LLM 提示词中的格式化文本，包含编号、标题、
        来源类型、基因名、期刊、DOI、PMID、链接和内容。

        Returns:
            多行格式化文本字符串。
        """
        lines = [f"[{self.source_id}] {self.title}", f"来源: {self.source_type}"]
        if self.gene_name:
            lines.append(f"基因: {self.gene_name}")
        if self.journal:
            lines.append(f"期刊: {self.journal}")
        if self.doi:
            lines.append(f"DOI: {self.doi}")
        if self.pmid:
            lines.append(f"PMID: {self.pmid}")
        if self.url:
            lines.append(f"链接: {self.url}")
        lines.append(f"内容: {self.content}")
        return "\n".join(lines)


@dataclass(frozen=True)
class EvidencePacket:
    """证据包，封装一次 RAG 检索的完整结果。

    包含查询信息、检索模式、证据条目列表、各来源统计和警告信息。

    Attributes:
        query: 原始查询字符串。
        mode: 检索模式（如 "normal"、"deep"）。
        items: 证据条目列表。
        source_counts: 各来源返回的结果数量统计。
        warnings: 警告信息列表（如某来源未返回结果）。
    """
    query: str
    mode: str
    items: list[EvidenceItem]
    source_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def citations(self) -> list[dict[str, Any]]:
        """获取所有证据条目的引用字典列表。

        Returns:
            由各 EvidenceItem.to_citation() 返回值组成的列表。
        """
        return [item.to_citation() for item in self.items]

    def to_tool_text(self) -> str:
        """将证据包格式化为工具调用返回的文本。

        生成适合作为 Agent 工具调用结果的完整格式化文本，
        包含检索摘要、来源统计、警告信息和所有证据条目的详细内容。

        Returns:
            完整的格式化 RAG 检索结果文本。
        """
        lines = [
            f"RAG 检索结果 query={self.query!r} mode={self.mode!r}",
            "要求：回答中引用这些证据时必须使用对应的 [编号]，不要自行重排编号。",
            "",
        ]
        if self.source_counts:
            counts = "；".join(f"{name}: {count}" for name, count in self.source_counts.items())
            lines.append(f"来源统计: {counts}")
        for warning in self.warnings:
            lines.append(f"注意: {warning}")
        if self.source_counts or self.warnings:
            lines.append("")
        if not self.items:
            lines.append("未找到可用证据。")
        for item in self.items:
            lines.append(item.to_prompt_block())
            lines.append("")
        return "\n".join(lines).strip()

    def __str__(self) -> str:
        """返回证据包的字符串表示，等同于 to_tool_text()。"""
        return self.to_tool_text()


def extract_graph_evidence(
    packet: EvidencePacket,
    *,
    max_graphs: int | None = None,
    max_edges: int = 40,
    max_nodes: int = 80,
) -> list[dict[str, Any]]:
    """从 EvidencePacket 中提取可公开展示的图 RAG 执行证据。

    这里展示的是工具检索到的节点、边和边上证据，不包含模型隐藏推理。
    图源优先在 metadata.paths 中提供 GraphPathSearchResult 形状；旧的 nodes/edges
    metadata 仍作为兼容输入支持，前端统一渲染成局部图。
    """
    graphs: list[dict[str, Any]] = []
    for item in packet.items:
        if item.source_type != "graph_db":
            continue
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        nodes, edges = _graph_nodes_edges(metadata)
        if not nodes or not edges:
            continue

        edges = edges[:max_edges]
        nodes = _limit_graph_nodes(nodes, edges, max_nodes=max_nodes)
        if not nodes:
            continue

        graphs.append(
            {
                "query": metadata.get("query") or packet.query,
                "backend": metadata.get("backend") or "graph_db",
                "seeds": _as_list(metadata.get("seeds") or metadata.get("starts") or metadata.get("targets")),
                "nodes": nodes,
                "edges": edges,
                "direction": metadata.get("direction") or "",
                "hops": metadata.get("hops") or metadata.get("max_hops") or "",
            }
        )
        if max_graphs is not None and len(graphs) >= max_graphs:
            break
    return graphs


def summarize_graph_evidence(graphs: list[dict[str, Any]]) -> str:
    """生成工具折叠区使用的短文本摘要。"""
    if not graphs:
        return ""
    parts = []
    for graph in graphs[:2]:
        seeds = graph.get("seeds") or []
        seed_names = [
            clean_text(seed.get("name") or seed.get("id") or "")
            for seed in seeds
            if isinstance(seed, dict)
        ]
        seed_text = "、".join(seed_names[:4]) or "未明确匹配主体"
        direction = graph.get("direction") or "both"
        hops = graph.get("hops") or "?"
        backend = graph.get("backend") or "graph_db"
        parts.append(
            f"图 RAG 检索主体: {seed_text}; 方向: {direction}; hops: {hops}; "
            f"backend: {backend}; 返回 {len(graph.get('nodes') or [])} 个节点、{len(graph.get('edges') or [])} 条边"
        )
    return " | ".join(parts)


def _graph_nodes_edges(metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = _as_list(metadata.get("nodes"))
    edges = _as_list(metadata.get("edges"))
    if nodes and edges:
        return [_clean_node(node) for node in nodes], [_clean_edge(edge) for edge in edges]

    path_nodes: dict[str, dict[str, Any]] = {}
    path_edges: list[dict[str, Any]] = []
    for path in _as_list(metadata.get("paths")):
        if not isinstance(path, dict):
            continue
        for node in _as_list(path.get("nodes")):
            clean = _clean_node(node)
            if clean.get("id"):
                path_nodes.setdefault(clean["id"], clean)
        for rel in _as_list(path.get("relationships")):
            edge = _clean_edge(
                {
                    **rel,
                    "src": rel.get("src") or rel.get("source_id"),
                    "dst": rel.get("dst") or rel.get("target_id"),
                    "relation": rel.get("relation") or rel.get("type"),
                }
            )
            if edge.get("src") and edge.get("dst"):
                path_edges.append(edge)
    return list(path_nodes.values()), path_edges


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else list(value) if isinstance(value, tuple) else []


def _clean_node(node: Any) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    return {
        key: clean_text(node.get(key))
        for key in ("id", "type", "name", "species")
        if clean_text(node.get(key))
    }


def _clean_edge(edge: Any) -> dict[str, Any]:
    if not isinstance(edge, dict):
        return {}
    evidence = edge.get("evidence") if isinstance(edge.get("evidence"), dict) else {}
    cleaned = {
        "id": clean_text(edge.get("id")),
        "src": clean_text(edge.get("src") or edge.get("source_id")),
        "dst": clean_text(edge.get("dst") or edge.get("target_id")),
        "relation": clean_text(edge.get("relation") or edge.get("type")),
        "evidence": {
            key: clean_text(evidence.get(key))
            for key in ("doi", "summary", "validation", "section", "journal", "title", "source_file", "mode")
            if clean_text(evidence.get(key))
        },
    }
    return {key: value for key, value in cleaned.items() if value not in ("", {})}


def _limit_graph_nodes(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], *, max_nodes: int) -> list[dict[str, Any]]:
    edge_node_ids = []
    for edge in edges:
        for key in ("src", "dst"):
            node_id = edge.get(key)
            if node_id and node_id not in edge_node_ids:
                edge_node_ids.append(node_id)

    by_id = {node.get("id"): node for node in nodes if node.get("id")}
    output = [by_id[node_id] for node_id in edge_node_ids if node_id in by_id]
    if len(output) < min(max_nodes, len(nodes)):
        seen = {node.get("id") for node in output}
        for node in nodes:
            if len(output) >= max_nodes:
                break
            if node.get("id") and node.get("id") not in seen:
                output.append(node)
                seen.add(node.get("id"))
    return output[:max_nodes]


class EvidenceFusion:
    """多来源证据融合器：合并来自不同检索源的结果，同时确保每个来源都有覆盖。"""

    def fuse(self, results_by_source: dict[str, list[EvidenceItem]], top_k: int) -> list[EvidenceItem]:
        """融合多来源检索结果。

        首先从每个来源取一条最佳结果以保证覆盖度，然后按评分从高到低
        填充剩余名额，最后进行去重处理。

        Args:
            results_by_source: 按来源名称分组的证据列表字典。
            top_k: 最终返回的最大条目数。

        Returns:
            融合并去重后的证据列表，长度不超过 top_k。
        """
        nonempty = {source: items for source, items in results_by_source.items() if items}
        if not nonempty:
            return []

        output: list[EvidenceItem] = []
        for source, items in nonempty.items():
            if len(output) >= top_k:
                break
            output.append(items[0])

        candidates = [
            item
            for items in nonempty.values()
            for item in items
            if item not in output
        ]
        candidates.sort(key=lambda item: item.score, reverse=True)
        output.extend(candidates[: max(top_k - len(output), 0)])

        return self._dedupe(output)[:top_k]

    @staticmethod
    def _dedupe(items: list[EvidenceItem]) -> list[EvidenceItem]:
        """对证据列表进行去重。

        根据 evidence_key（DOI/PMID/URL/标题）识别重复项，
        保留首次出现的条目，跳过空标题的条目。

        Args:
            items: 待去重的证据列表。

        Returns:
            去重后的证据列表，保持原始顺序。
        """
        output: list[EvidenceItem] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = evidence_key(title=item.title, doi=item.doi, pmid=item.pmid, url=item.url)
            if key == ("title", ""):
                continue
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
        return output


class SourceCollector:
    """来源收集器：为融合后的证据条目分配稳定的答案级别引用编号。"""

    def assign(self, items: list[EvidenceItem]) -> list[EvidenceItem]:
        """为证据列表分配从 1 开始的连续引用编号。

        对输入列表进行去重（基于 DOI/PMID/URL/标题），并为每条唯一的
        证据分配一个从 1 开始递增的 source_id。

        Args:
            items: 待编号的证据列表。

        Returns:
            去重并重新编号后的证据列表。
        """
        output: list[EvidenceItem] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = evidence_key(title=item.title, doi=item.doi, pmid=item.pmid, url=item.url)
            if key == ("title", ""):
                continue
            if key in seen:
                continue
            seen.add(key)
            output.append(
                EvidenceItem(
                    source_id=str(len(output) + 1),
                    source_type=item.source_type,
                    title=item.title,
                    content=item.content,
                    url=item.url,
                    doi=item.doi,
                    pmid=item.pmid,
                    journal=item.journal,
                    gene_name=item.gene_name,
                    score=item.score,
                    metadata=item.metadata,
                )
            )
        return output


class CitationRegistry:
    """全局引用编号注册表：在一次 Agent.run 生命周期内统一管理文献引用编号。

    问题背景：
    - Agent 在一次对话中可能多次调用 rag_search 工具
    - 每次 RAG 搜索返回的 EvidencePacket 都有自己的局部编号（如 [1], [2], [3]...）
    - 如果不统一编号，同一篇文献在不同搜索中会被分配不同编号，导致引用混乱

    解决方案：
    - 使用 CitationRegistry 维护一个全局的文献→编号映射表
    - 相同文献（通过 DOI/PMID/URL/标题识别）始终使用相同编号
    - 新文献按出现顺序分配递增编号

    示例：
        第一次搜索返回: [1] Paper A, [2] Paper B
        第二次搜索返回: [1] Paper B, [2] Paper C
        经过 Registry 处理后:
            第一次: [1] Paper A, [2] Paper B
            第二次: [2] Paper B, [3] Paper C  # Paper B 复用编号 2，Paper C 获得新编号 3
    """

    def __init__(self) -> None:
        """初始化引用编号注册表。

        创建空的文献标识到全局编号的映射字典。
        """
        # 核心数据结构：文献唯一标识 -> 全局编号
        # key: (标识类型, 标识值)，如 ("doi", "10.1038/xxx") 或 ("title", "normalized title")
        # value: 全局编号字符串，如 "1", "2", "3"...
        self._ids_by_key: dict[tuple[str, str], str] = {}

    def assign_packet(self, packet: EvidencePacket) -> EvidencePacket:
        """为整个证据包重新分配全局编号。

        Args:
            packet: 来自 RAG 搜索的原始证据包（带局部编号）

        Returns:
            新的证据包，其中所有 EvidenceItem 的 source_id 已替换为全局编号
        """
        return EvidencePacket(
            query=packet.query,
            mode=packet.mode,
            items=[self._assign_item(item) for item in packet.items],  # 逐个处理每条证据
            source_counts=packet.source_counts,
            warnings=packet.warnings,
        )

    def _assign_item(self, item: EvidenceItem) -> EvidenceItem:
        """为单条证据分配全局编号。

        工作流程：
        1. 根据 DOI/PMID/URL/标题生成唯一标识 key
        2. 如果 key 已存在于映射表，复用旧编号
        3. 如果 key 是新的，分配下一个可用编号（当前表大小 + 1）
        4. 创建新的 EvidenceItem，只替换 source_id，其他字段保持不变

        Args:
            item: 原始证据条目（带局部编号）

        Returns:
            新的证据条目（带全局编号）
        """
        # 生成文献唯一标识（优先级：DOI > PMID > URL > 标题）
        key = evidence_key(title=item.title, doi=item.doi, pmid=item.pmid, url=item.url)

        # 特殊情况：如果连标题都无法提取（空标题），保留原编号
        if key == ("title", ""):
            source_id = item.source_id
        else:
            # 核心逻辑：查找或创建全局编号
            # setdefault: 如果 key 存在返回旧值，否则插入新值并返回
            # 新值 = str(len(self._ids_by_key) + 1)，即下一个可用编号
            source_id = self._ids_by_key.setdefault(key, str(len(self._ids_by_key) + 1))

        # 创建新的 EvidenceItem，只替换 source_id
        return EvidenceItem(
            source_id=source_id,  # 唯一改变的字段
            source_type=item.source_type,
            title=item.title,
            content=item.content,
            url=item.url,
            doi=item.doi,
            pmid=item.pmid,
            journal=item.journal,
            gene_name=item.gene_name,
            score=item.score,
            metadata=item.metadata,
        )
