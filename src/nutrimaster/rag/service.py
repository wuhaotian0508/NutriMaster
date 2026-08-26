from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from nutrimaster.rag.evidence import EvidenceFusion, EvidenceItem, EvidencePacket, SourceCollector

logger = logging.getLogger(__name__)
PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


@dataclass(frozen=True)
class RAGSearchContext:
    """RAG 搜索上下文配置，控制单次搜索的行为参数。

    Attributes:
        user_id: 用户 ID（用于个人文库搜索）。
        include_personal: 是否包含个人文库搜索结果。
        mode: 搜索模式，"normal" 或 "deep"（深度模式返回更多结果）。
        focus: 搜索聚焦方向（如 "general"、"mechanism" 等）。
        top_k: 最终返回的最大结果数。
        pubmed_query: 用于 PubMed 的自定义查询（为空则使用主查询）。
        gene_db_query: 用于基因库的自定义查询（为空则使用主查询）。
        gene_db_keyword_spec: Agent 生成的字段关键词检索规格，用于本地基因库精确补召回。
    """
    user_id: str | None = None
    include_personal: bool = False
    mode: str = "normal"
    focus: str = "general"
    top_k: int = 10
    pubmed_query: str = ""
    gene_db_query: str = ""
    gene_db_keyword_spec: Any | None = None


class RAGSearchService:
    """组合证据搜索服务：供 Agent 的 rag_search 工具调用。

    聚合来自 PubMed、本地基因数据库和个人文库三个来源的搜索结果，
    通过融合和去重生成统一的证据包。
    """

    def __init__(
        self,
        *,
        pubmed_source: Any,
        gene_db_source: Any,
        graph_source: Any | None = None,
        personal_source: Any | None = None,
        fusion: EvidenceFusion | None = None,
        source_collector: SourceCollector | None = None,
    ):
        """初始化 RAG 搜索服务。

        Args:
            pubmed_source: PubMed 检索源实例。
            gene_db_source: 基因数据库检索源实例。
            personal_source: 个人文库检索源实例（可选）。
            fusion: 证据融合器（可选，默认使用 EvidenceFusion）。
            source_collector: 来源收集器（可选，默认使用 SourceCollector）。
        """
        self.pubmed_source = pubmed_source
        self.gene_db_source = gene_db_source
        self.graph_source = graph_source
        self.personal_source = personal_source
        self.fusion = fusion or EvidenceFusion()
        self.source_collector = source_collector or SourceCollector()

    async def search(self, query: str, context: RAGSearchContext | None = None) -> EvidencePacket:
        """执行多来源组合搜索。

        并行查询 PubMed 和基因数据库（可选个人文库），然后融合、去重、
        编号后返回统一的证据包。

        Args:
            query: 搜索查询文本。
            context: 搜索上下文配置（可选，默认使用默认配置）。

        Returns:
            包含融合后证据的 EvidencePacket。
        """
        context = context or RAGSearchContext()
        source_budget = self._source_budget(context)
        pubmed_query = (context.pubmed_query or query).strip()
        gene_db_query = (context.gene_db_query or query).strip()
        tasks = {
            "pubmed": self._safe_search(self.pubmed_source, pubmed_query, top_k=source_budget["pubmed"], context=context),
            "gene_db": self._safe_search(self.gene_db_source, gene_db_query, top_k=source_budget["gene_db"], context=context),
        }
        if self.graph_source is not None:
            tasks["graph_db"] = self._safe_search(
                self.graph_source,
                gene_db_query,
                top_k=source_budget["graph_db"],
                context=context,
            )
        if context.include_personal and self.personal_source is not None:
            tasks["personal"] = self._safe_search(
                self.personal_source,
                query,
                top_k=source_budget["personal"],
                context=context,
            )

        keys = list(tasks)
        running = [asyncio.create_task(search) for search in tasks.values()]
        try:
            results = await asyncio.gather(*running)
        except MemoryError:
            # ``gather`` does not cancel siblings when one child raises. Stop
            # PubMed/graph/personal work before propagating allocator pressure.
            for task in running:
                task.cancel()
            for task in running:
                try:
                    await task
                except BaseException:
                    pass
            raise
        results_by_source = dict(zip(keys, results))
        source_counts = {key: len(items) for key, items in results_by_source.items()}
        warnings = self._empty_source_warnings(source_counts)

        fused = self.fusion.fuse(results_by_source, top_k=context.top_k)
        numbered = self.source_collector.assign(fused)
        return EvidencePacket(
            query=query,
            mode=context.mode,
            items=numbered,
            source_counts=source_counts,
            warnings=warnings,
        )

    @staticmethod
    def _source_budget(context: RAGSearchContext) -> dict[str, int]:
        """根据搜索模式分配各来源的结果配额。

        深度模式（deep）分配更多配额以获取更全面的结果。

        Args:
            context: 搜索上下文配置。

        Returns:
            各来源名称到配额数量的映射字典。
        """
        if context.mode == "deep":
            return {"pubmed": 12, "gene_db": 20, "graph_db": 8, "personal": 12}
        return {"pubmed": 6, "gene_db": 12, "graph_db": 4, "personal": 8}

    @staticmethod
    def _empty_source_warnings(source_counts: dict[str, int]) -> list[str]:
        """检查各来源搜索结果，为空结果的来源生成警告信息。

        Args:
            source_counts: 各来源的结果数量。

        Returns:
            警告信息字符串列表。
        """
        warnings = []
        if source_counts.get("pubmed") == 0:
            warnings.append("PubMed 未返回结果；如需重试，请重新调用 rag_search 并提供英文 pubmed_query。")
        if source_counts.get("gene_db") == 0:
            warnings.append("本地基因库未返回结果；如需重试，请重新调用 rag_search 并优化 query 或 gene_db_query。")
        return warnings

    @staticmethod
    async def _safe_search(source: Any, query: str, *, top_k: int, context: RAGSearchContext) -> list[EvidenceItem]:
        """安全地执行单个来源的搜索，捕获并记录异常。

        即使某个来源搜索失败，也不会影响整体流程，返回空列表。

        Args:
            source: 检索源实例。
            query: 搜索查询文本。
            top_k: 最大返回数量。
            context: 搜索上下文配置。

        Returns:
            搜索到的证据列表；若搜索失败则返回空列表。
        """
        try:
            return await source.search(
                query,
                top_k=top_k,
                user_id=context.user_id,
                mode=context.mode,
                focus=context.focus,
                keyword_spec=context.gene_db_keyword_spec,
            )
        except MemoryError:
            # Do not treat allocator exhaustion as an empty source and continue
            # into the remaining fusion/LLM stages.
            raise
        except Exception as exc:
            logger.warning("%s search failed: %s: %r", source.__class__.__name__, type(exc).__name__, exc)
            return []


class PubMedSource:
    """PubMed 检索源：通过 NCBI E-utilities API 搜索生物医学文献。

    使用 esearch + efetch 两步查询获取文献元信息和摘要。
    """

    source_type = "pubmed"

    def __init__(self, *, http_client_factory=None):
        """初始化 PubMed 检索源。

        Args:
            http_client_factory: HTTP 客户端工厂函数（可选，默认创建 httpx.AsyncClient）。
        """
        self._http_client_factory = http_client_factory or (lambda: httpx.AsyncClient(timeout=20))

    async def search(self, query: str, *, top_k: int = 8, **_: Any) -> list[EvidenceItem]:
        """搜索 PubMed 文献并返回证据列表。

        Args:
            query: 搜索查询文本。
            top_k: 最大返回数量，默认 8。
            **_: 忽略的额外参数（兼容统一搜索接口）。

        Returns:
            包含标题、摘要和元信息的 EvidenceItem 列表。
        """
        articles = await self._search_raw(query, max_results=top_k)
        return [
            EvidenceItem(
                source_id="",
                source_type=self.source_type,
                title=article["title"],
                content=article["abstract"],
                url=article["url"],
                pmid=article["pmid"],
                journal=article["journal"],
                score=0.0,
                metadata={"pmid": article["pmid"], "journal": article["journal"]},
            )
            for article in articles
        ]

    async def _search_raw(self, query: str, max_results: int) -> list[dict[str, Any]]:
        """通过 NCBI E-utilities 执行原始 PubMed 搜索。

        先用 esearch 获取文献 ID 列表，再用 efetch 获取详细信息。

        Args:
            query: PubMed 搜索查询。
            max_results: 最大返回数量。

        Returns:
            包含 pmid、title、abstract、journal、url 的字典列表。
        """
        client = self._http_client_factory()
        close = getattr(client, "aclose", None)
        try:
            response = await client.get(
                PUBMED_ESEARCH_URL,
                params={
                    "db": "pubmed",
                    "term": query,
                    "retmax": max_results,
                    "retmode": "json",
                    "sort": "relevance",
                },
            )
            response.raise_for_status()
            ids = response.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                return []
            response = await client.get(
                PUBMED_EFETCH_URL,
                params={
                    "db": "pubmed",
                    "id": ",".join(ids),
                    "retmode": "xml",
                    "rettype": "abstract",
                },
            )
            response.raise_for_status()
            return self._parse_pubmed_xml(response.text)
        finally:
            if close is not None:
                await close()

    @staticmethod
    def _parse_pubmed_xml(xml_text: str) -> list[dict[str, str]]:
        """解析 PubMed efetch 返回的 XML 文本。

        提取每篇文献的 PMID、标题、摘要、期刊和 URL。

        Args:
            xml_text: PubMed XML 格式的响应文本。

        Returns:
            解析后的文献信息字典列表。
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        results = []
        for article in root.iter("PubmedArticle"):
            try:
                medline = article.find("MedlineCitation")
                article_element = medline.find("Article") if medline is not None else None
                if medline is None or article_element is None:
                    continue
                pmid_element = medline.find("PMID")
                title_element = article_element.find("ArticleTitle")
                abstract_parts = []
                abstract_element = article_element.find("Abstract")
                if abstract_element is not None:
                    for text_element in abstract_element.findall("AbstractText"):
                        label = text_element.get("Label", "")
                        text = "".join(text_element.itertext()).strip()
                        abstract_parts.append(f"{label}: {text}" if label else text)
                journal_element = article_element.find("Journal/Title")
                pmid = pmid_element.text if pmid_element is not None else ""
                results.append(
                    {
                        "pmid": pmid,
                        "title": "".join(title_element.itertext()).strip() if title_element is not None else "",
                        "abstract": "\n".join(abstract_parts) or "(no abstract)",
                        "journal": journal_element.text if journal_element is not None else "",
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    }
                )
            except MemoryError:
                raise
            except Exception:
                continue
        return results


class GeneDbSource:
    """本地基因数据库检索源：从已索引的基因信息块中检索相关基因。"""

    source_type = "gene_db"

    def __init__(self, retriever: Any):
        """初始化基因数据库检索源。

        Args:
            retriever: 底层检索器实例（如 JinaRetriever），需支持 search 或 hybrid_search 方法。
        """
        self.retriever = retriever

    async def search(
        self,
        query: str,
        *,
        top_k: int = 12,
        keyword_spec: Any | None = None,
        **_: Any,
    ) -> list[EvidenceItem]:
        """在本地基因数据库中搜索与查询相关的基因信息。

        优先使用混合搜索（hybrid_search），若不可用则回退到普通搜索。
        将检索到的 GeneChunk 转换为统一的 EvidenceItem 格式。

        Args:
            query: 搜索查询文本。
            top_k: 最大返回数量，默认 12。
            **_: 忽略的额外参数。

        Returns:
            包含基因信息的 EvidenceItem 列表，按相关性排序。
        """
        import inspect

        if hasattr(self.retriever, "hybrid_search"):
            kwargs = {
                "top_k": top_k,
                "rerank": True,
                "rerank_top_n": max(top_k * 4, 50),
            }
            signature = inspect.signature(self.retriever.hybrid_search)
            accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
            if keyword_spec is not None and (accepts_kwargs or "keyword_spec" in signature.parameters):
                kwargs["keyword_spec"] = keyword_spec
            maybe_results = self.retriever.hybrid_search(query, **kwargs)
            results = await maybe_results if inspect.isawaitable(maybe_results) else maybe_results
        else:
            results = await asyncio.to_thread(self.retriever.search, query, top_k=top_k)
        return [
            EvidenceItem(
                source_id="",
                source_type=self.source_type,
                title=getattr(chunk, "paper_title", "") or "",
                content=getattr(chunk, "content", "") or "",
                doi=getattr(chunk, "doi", "") or "",
                journal=getattr(chunk, "journal", "") or "",
                gene_name=getattr(chunk, "gene_name", "") or "",
                score=float(score),
                metadata={
                    "gene_type": getattr(chunk, "gene_type", "") or "",
                    "chunk_type": getattr(chunk, "chunk_type", "") or "",
                },
            )
            for chunk, score in results
        ]


class PersonalLibrarySource:
    """个人文库检索源：从用户上传的 PDF 文档中检索相关内容。"""

    source_type = "personal"

    def __init__(
        self,
        *,
        get_personal_lib: Callable[[str], Any] | None = None,
        get_query_embedding: Callable[[str], Any] | None = None,
    ):
        """初始化个人文库检索源。

        Args:
            get_personal_lib: 根据 user_id 获取个人文库实例的函数。
            get_query_embedding: 将查询文本转换为嵌入向量的函数。
        """
        self.get_personal_lib = get_personal_lib
        self.get_query_embedding = get_query_embedding

    async def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        user_id: str | None = None,
        **_: Any,
    ) -> list[EvidenceItem]:
        """在用户的个人文库中搜索相关内容。

        需要提供 user_id 以及有效的文库获取和嵌入函数才能执行搜索。

        Args:
            query: 搜索查询文本。
            top_k: 最大返回数量，默认 8。
            user_id: 用户 ID（必需，为 None 时返回空列表）。
            **_: 忽略的额外参数。

        Returns:
            包含文档片段的 EvidenceItem 列表。
        """
        if not user_id or self.get_personal_lib is None or self.get_query_embedding is None:
            return []
        library = self.get_personal_lib(user_id)
        query_embedding = await asyncio.to_thread(self.get_query_embedding, query)
        results = await asyncio.to_thread(library.search, query_embedding, top_k=top_k)
        output: list[EvidenceItem] = []
        for item in results:
            metadata = item.get("metadata", {})
            filename = metadata.get("filename", item.get("title", ""))
            page = metadata.get("page", "")
            output.append(
                EvidenceItem(
                    source_id="",
                    source_type=self.source_type,
                    title=f"{filename} (p.{page})" if page else filename,
                    content=item.get("content", ""),
                    score=float(item.get("score", 0.0)),
                    metadata=metadata,
                )
            )
        return output
