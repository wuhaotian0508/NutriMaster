"""
NutriMaster Agent - 基于 RAG 的营养代谢基因知识问答系统
"""

import asyncio
import threading
from typing import Any


class NutriMasterAgent:
    """NutriMaster RAG Agent"""

    def __init__(self, model_id: str = "", use_depth: bool = True):
        self.model_id = model_id
        self.use_depth = use_depth
        self.name = "NutriMaster"
        self._services = None
        self._lock = threading.Lock()

    def _get_services(self):
        """延迟加载 NutriMaster 服务"""
        if self._services is None:
            with self._lock:
                if self._services is None:
                    from nutrimaster.web.deps import create_services
                    self._services = create_services()
        return self._services

    async def answer(self, question: str) -> dict[str, Any]:
        """
        回答问题

        返回: {"ok": True/False, "output": "答案", "error": "错误信息"}
        """
        try:
            services = await asyncio.to_thread(self._get_services)
            text_parts = []
            citations = []
            errors = []

            async for event in services.agent.run(
                user_input=question,
                user_id=None,
                model_id=self.model_id,
                history=[],
                use_personal=False,
                use_depth=self.use_depth,
                skill_prefs={},
                tool_overrides={},
            ):
                event_type = event.get("type")
                if event_type == "text":
                    text_parts.append(event.get("data", ""))
                elif event_type == "citations":
                    citations = event.get("data", []) or []
                elif event_type == "error":
                    errors.append(str(event.get("data", "")))

            output = self._format_output("\n".join(text_parts), citations)
            if not output.strip():
                error = "; ".join(err for err in errors if err) or "NutriMaster 返回空内容"
                return {"ok": False, "error": error, "output": ""}

            return {"ok": True, "output": output, "error": None}

        except Exception as e:
            return {"ok": False, "error": str(e), "output": ""}

    def _format_output(self, answer: str, citations: list[dict[str, Any]]) -> str:
        """格式化输出（包含引用）"""
        output = answer.strip()
        if not citations:
            return output

        lines = ["", "参考来源:"]
        for index, citation in enumerate(citations, start=1):
            tool_index = citation.get("tool_index") or citation.get("source_id") or index
            title = citation.get("title") or citation.get("paper_title") or "(untitled)"
            details = []
            for key, label in (
                ("source_type", "source"),
                ("gene_name", "gene"),
                ("journal", "journal"),
                ("pmid", "PMID"),
                ("doi", "DOI"),
                ("url", "URL"),
            ):
                value = citation.get(key)
                if value:
                    details.append(f"{label}={value}")
            suffix = f" {'; '.join(details)}" if details else ""
            lines.append(f"[{tool_index}] {title}{suffix}")

        return output + "\n".join(lines)
