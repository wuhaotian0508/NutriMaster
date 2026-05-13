from __future__ import annotations

# 基础工具类定义: src/nutrimaster/agent/tools/base.py (第11-26行)
from nutrimaster.agent.tools.base import BaseTool
# 证据包数据结构: src/nutrimaster/rag/evidence.py (第134-167行)
# EvidencePacket 数据类定义：
#   @dataclass(frozen=True)
#   class EvidencePacket:
#       query: str
#       mode: str
#       items: list[EvidenceItem]
#       source_counts: dict[str, int] = field(default_factory=dict)
#       warnings: list[str] = field(default_factory=list)
from nutrimaster.rag.evidence import EvidencePacket
# RAG 检索服务实现: src/nutrimaster/rag/service.py
# RAGSearchContext 数据类 (第19-28行):
#   @dataclass(frozen=True)
#   class RAGSearchContext:
#       user_id: str | None = None
#       include_personal: bool = False
#       mode: str = "normal"
#       focus: str = "general"
#       top_k: int = 10
#       pubmed_query: str = ""
#       gene_db_query: str = ""
# RAGSearchService.search() 方法 (第48-79行):
#   async def search(self, query: str, context: RAGSearchContext | None = None) -> EvidencePacket:
#       context = context or RAGSearchContext()
#       source_budget = self._source_budget(context)
#       pubmed_query = (context.pubmed_query or query).strip()
#       gene_db_query = (context.gene_db_query or query).strip()
#       tasks = {
#           "pubmed": self._safe_search(self.pubmed_source, pubmed_query, top_k=source_budget["pubmed"], context=context),
#           "gene_db": self._safe_search(self.gene_db_source, gene_db_query, top_k=source_budget["gene_db"], context=context),
#       }
#       if context.include_personal and self.personal_source is not None:
#           tasks["personal"] = self._safe_search(self.personal_source, query, top_k=source_budget["personal"], context=context)
#       keys = list(tasks)
#       results = await asyncio.gather(*tasks.values())
#       results_by_source = dict(zip(keys, results))
#       source_counts = {key: len(items) for key, items in results_by_source.items()}
#       warnings = self._empty_source_warnings(source_counts)
#       fused = self.fusion.fuse(results_by_source, top_k=context.top_k)
#       numbered = self.source_collector.assign(fused)
#       return EvidencePacket(query=query, mode=context.mode, items=numbered,
#                             source_counts=source_counts, warnings=warnings)
from nutrimaster.rag.service import RAGSearchContext, RAGSearchService


class RagSearchTool(BaseTool):
    """复合 RAG 检索工具，同时检索 PubMed 摘要和本地基因库，返回带编号的证据。

    封装 RAGSearchService，支持多源检索、个人知识库、深度搜索等功能。
    """
    name = "rag_search"
    description = "复合 RAG 检索：检索 PubMed 摘要和本地基因库，必要时加入个人库，并返回带编号证据"

    def __init__(self, service: RAGSearchService):
        """初始化 RAG 检索工具。

        参数:
            service: RAG 检索服务实例，提供底层的多源检索和融合功能。
        """
        self.service = service

    @property
    def schema(self) -> dict:
        """获取 RAG 检索工具的 OpenAI 兼容函数调用 schema。

        返回:
            dict: 包含 query（必填）、pubmed_query、gene_db_query、mode、
                  include_personal、focus、top_k 参数的 schema 定义。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "本地基因库的语义检索词。保留关键基因、通路、物种、代谢物，可用中英混合。",
                        },
                        "pubmed_query": {
                            "type": "string",
                            "description": "可选。Agent 自行生成的英文 PubMed 检索式/关键词，如 HY5 AND alkaloid AND photoreceptor。",
                        },
                        "gene_db_query": {
                            "type": "string",
                            "description": "可选。本地基因库专用检索词；不填时使用 query。",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["normal", "deep"],
                            "description": "normal 或 deep。两者都会检索 PubMed 和基因库，deep 会增加召回预算。",
                        },
                        "include_personal": {
                            "type": "boolean",
                            "description": "是否加入个人知识库。用户未开启个人库时保持 false。",
                        },
                        "focus": {
                            "type": "string",
                            "description": "检索重点，如 gene_function、pathway、literature、experiment、general。",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "融合后返回证据数量，默认 10。",
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    async def execute(
        self,
        query: str,
        pubmed_query: str = "",
        gene_db_query: str = "",
        mode: str = "normal",
        include_personal: bool = False,
        focus: str = "general",
        top_k: int = 10,
        user_id: str | None = None,
        **_,
    ) -> EvidencePacket:
        """执行复合 RAG 检索，返回融合后的证据包。

        同时检索 PubMed 和本地基因库，可选加入个人知识库，
        对结果进行融合排序并编号。

        参数:
            query: 本地基因库的语义检索词。
            pubmed_query: PubMed 专用英文检索式/关键词，为空时使用 query。
            gene_db_query: 本地基因库专用检索词，为空时使用 query。
            mode: 检索模式，"normal" 或 "deep"（增加召回预算）。
            include_personal: 是否加入个人知识库检索。
            focus: 检索重点，如 gene_function、pathway、literature 等。
            top_k: 融合后返回的证据数量。
            user_id: 用户标识符，用于个人库检索。
            **_: 忽略的额外参数。

        返回:
            EvidencePacket: 包含融合后证据列表、来源统计和警告信息的证据包。
        """
        return await self.service.search(
            query,
            RAGSearchContext(
                user_id=user_id,
                include_personal=include_personal,
                mode=mode,
                focus=focus,
                top_k=top_k,
                pubmed_query=pubmed_query,
                gene_db_query=gene_db_query,
            ),
        )
