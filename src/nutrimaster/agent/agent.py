from __future__ import annotations

import asyncio
import json
import logging
import re

from nutrimaster.agent.prompts import PromptBuilder
from nutrimaster.experiment import extract_gene_names
from nutrimaster.experiment.gene_validation import extract_transgenic_species_with_llm
from nutrimaster.rag.evidence import CitationRegistry, EvidencePacket, evidence_key

logger = logging.getLogger(__name__)

MAX_STEPS = 12


class Agent:
    """基于 LLM 的工具调用循环代理，用于协调 NutriMaster 高级工具的调用与交互。"""

    def __init__(self, registry, skill_loader, call_llm, prompt_builder: PromptBuilder | None = None):
        """初始化 Agent 实例。

        参数:
            registry: 工具注册表，管理可用的工具集合。
            skill_loader: 技能加载器，用于加载共享和用户自定义技能。
            call_llm: 异步回调函数，用于调用 LLM 模型生成回复。
            prompt_builder: 提示词构建器，为 None 时使用默认的 PromptBuilder。
        """
        self.registry = registry
        self.loader = skill_loader
        self.call_llm = call_llm
        self.prompt_builder = prompt_builder or PromptBuilder(skill_loader)

    @staticmethod
    def _msg_to_dict(msg, model_id: str = "") -> dict:
        """将 LLM 返回的消息对象转换为标准字典格式。

        参数:
            msg: LLM 返回的消息对象，支持 Pydantic 模型、字典或带属性的对象。
            model_id: 模型标识符（当前未使用，保留扩展）。

        返回:
            dict: 包含 role、content 等字段的标准消息字典。
        """
        if hasattr(msg, "model_dump"):
            data = msg.model_dump(exclude_none=True)
        elif isinstance(msg, dict):
            data = dict(msg)
        else:
            data = {
                key: getattr(msg, key)
                for key in ("role", "content", "tool_calls", "reasoning_content")
                if hasattr(msg, key) and getattr(msg, key) is not None
            }
        data.setdefault("role", "assistant")
        return data

    @staticmethod
    def _truncate_history(history: list, max_chars_per_msg: int = 800) -> list:
        """截断历史消息中过长的助手回复，防止上下文窗口溢出。

        参数:
            history: 历史消息列表，每个元素为包含 role 和 content 的字典。
            max_chars_per_msg: 每条助手消息的最大字符数，超出部分将被截断。

        返回:
            list: 截断处理后的消息列表。
        """
        truncated = []
        for msg in history or []:
            content = msg.get("content", "")
            if msg.get("role") == "assistant" and isinstance(content, str) and len(content) > max_chars_per_msg:
                truncated.append({**msg, "content": content[:max_chars_per_msg] + "\n...(之前回答已截断)"})
            else:
                truncated.append(msg)
        return truncated

    @staticmethod
    def _strip_reasoning_for_new_turn(messages: list[dict]) -> list[dict]:
        """移除助手消息中的 reasoning_content 字段，避免在新一轮对话中发送推理内容。

        参数:
            messages: 消息列表，每个元素为包含角色和内容的字典。

        返回:
            list[dict]: 移除 reasoning_content 后的消息列表。
        """
        output = []
        for msg in messages:
            if msg.get("role") == "assistant" and "reasoning_content" in msg:
                output.append({key: value for key, value in msg.items() if key != "reasoning_content"})
            else:
                output.append(msg)
        return output

    @staticmethod
    def _filter_citations(answer_text: str, evidence_packets: list[EvidencePacket]) -> list[dict]:
        """根据回答文本中实际引用的编号，过滤出被引用的文献条目。

        如果回答文本中包含 [数字] 形式的引用，则只保留被引用的文献；
        如果没有检测到引用编号，则返回所有文献。

        参数:
            answer_text: 代理生成的回答文本。
            evidence_packets: 证据包列表，包含 RAG 检索返回的文献引用。

        返回:
            list[dict]: 过滤后的文献引用列表。
        """
        citations = Agent._unique_citations(
            citation
            for packet in evidence_packets
            for citation in packet.citations
        )
        if not citations:
            return []
        cited_numbers = {
            int(match.group(1))
            for match in re.finditer(r"\[(\d+)\]", answer_text or "")
        }
        if not cited_numbers:
            return citations
        filtered = [
            citation
            for citation in citations
            if citation.get("tool_index") in cited_numbers
        ]
        return filtered or citations

    @staticmethod
    def _renumber_citations(answer_text: str, citations: list[dict]) -> tuple[str, list[dict]]:
        """按正文首次出现顺序对文献重新从 1 连续编号，并同步更新正文中的引用角标。"""
        known_indices = {c["tool_index"] for c in citations if c.get("tool_index")}
        # 按正文出现顺序收集唯一引用编号
        seen: list[int] = []
        seen_set: set[int] = set()
        for m in re.finditer(r"\[(\d+)\]", answer_text or ""):
            n = int(m.group(1))
            if n in known_indices and n not in seen_set:
                seen.append(n)
                seen_set.add(n)
        if not seen:
            return answer_text, citations
        renumber_map = {old: new for new, old in enumerate(seen, start=1)}
        citation_map = {c["tool_index"]: c for c in citations if c.get("tool_index")}
        new_citations = [
            {**citation_map[old], "tool_index": new,
             "source_id": str(new)}
            for old, new in renumber_map.items()
        ]
        new_text = re.sub(
            r"\[(\d+)\]",
            lambda m: f"[{renumber_map[int(m.group(1))]}]" if int(m.group(1)) in renumber_map else m.group(0),
            answer_text,
        )
        return new_text, new_citations

    @staticmethod
    def _unique_citations(citations) -> list[dict]:
        """对文献引用列表进行去重，基于标题、DOI、PMID 或 URL 作为唯一性标识。

        参数:
            citations: 可迭代的文献引用字典集合。

        返回:
            list[dict]: 去重后的文献引用列表。
        """
        output = []
        seen: set[tuple[str, str]] = set()
        for citation in citations:
            key = evidence_key(
                title=citation.get("title", ""),
                doi=citation.get("doi", ""),
                pmid=citation.get("pmid", ""),
                url=citation.get("url", ""),
            )
            if key == ("title", ""):
                key = ("source_id", str(citation.get("tool_index") or citation.get("source_id") or len(output) + 1))
            if key in seen:
                continue
            seen.add(key)
            output.append(citation)
        return output

    @staticmethod
    def _get_value(obj, key: str, default=None):
        """从字典或对象中安全获取指定键/属性的值。

        参数:
            obj: 目标字典或对象。
            key: 要获取的键名或属性名。
            default: 键/属性不存在时返回的默认值。

        返回:
            键/属性对应的值，不存在时返回 default。
        """
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    async def run(
        self,
        user_input: str,
        user_id: str | None = None,
        model_id: str = "",
        history: list | None = None,
        use_personal: bool = False,
        use_depth: bool = False,
        skill_prefs: dict | None = None,
        tool_overrides: dict | None = None,
    ):
        """执行代理的主循环，处理用户输入并通过工具调用生成回答。

        这是一个异步生成器方法，会依次 yield 各种事件（工具调用、文本、引用、错误等）。
        代理会在最多 MAX_STEPS 步内完成工具调用循环，直到 LLM 不再请求工具为止。

        参数:
            user_input: 用户输入的查询文本。
            user_id: 用户标识符，用于个人库检索和技能加载。
            model_id: 指定使用的 LLM 模型标识符。
            history: 对话历史消息列表。
            use_personal: 是否启用个人知识库检索。
            use_depth: 是否启用深度搜索模式。
            skill_prefs: 技能偏好配置（当前保留扩展）。
            tool_overrides: 工具覆盖配置（当前保留扩展）。

        生成(yields):
            dict: 包含 type 字段的事件字典，type 可为 tools_enabled、thinking、
                  text、tool_call、tool_result、citations、sources、genes_available、
                  done 或 error。
        """
        try:
            yield {"type": "tools_enabled", "tools": sorted(self.registry.tool_names)}
            messages = [
                {
                    "role": "system",
                    "content": self.prompt_builder.build(
                        user_id=user_id,
                        use_depth=use_depth,
                        use_personal=use_personal,
                    ),
                }
            ]
            messages.extend(self._truncate_history(history or []))
            messages.append({"role": "user", "content": user_input})
            messages = self._strip_reasoning_for_new_turn(messages)

            citation_registry = CitationRegistry()
            evidence_packets: list[EvidencePacket] = []
            all_answer_parts:list[str]=[]
            answer_text = ""
            for _step in range(MAX_STEPS):
                response = await self.call_llm(
                    messages,
                    tools=self.registry.get_definitions,
                    model_id=model_id,
                    is_agent_call=True,
                )
                assistant_message = self._msg_to_dict(response, model_id=model_id)
                reasoning_content=getattr(response,'reasoning_content',None) or assistant_message.get('reasoning_content')
                if reasoning_content:
                    yield{'type':'thinking','data':reasoning_content}
                
                assistant_message_for_context={k:v for k,v in assistant_message.items() if k!='reasoning_content'}
                messages.append(assistant_message_for_context)

                tool_calls = getattr(response, "tool_calls", None) or assistant_message.get("tool_calls") or []
                
                if not tool_calls:
                    step_content = response.content if hasattr(response, "content") else assistant_message.get("content", "")
                    if step_content:
                        all_answer_parts.append(step_content)
                        yield {"type": "text", "data": step_content}
                    break

                for tool_call in tool_calls:
                    function = self._get_value(tool_call, "function", {})
                    tool_name = self._get_value(function, "name")
                    raw_args = self._get_value(function, "arguments", "{}") or "{}"
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    if tool_name == "rag_search":
                        args.setdefault("include_personal", use_personal)
                        args.setdefault("mode", "deep" if use_depth else "normal")
                    if user_id:
                        args.setdefault("user_id", user_id)
                    yield {"type": "tool_call", "tool": tool_name, "args": args}
                    try:
                        result = await self.registry.execute(tool_name, **args)
                    except Exception as exc:
                        result = f"工具执行失败: {exc}"
                    if isinstance(result, EvidencePacket):
                        global_packet = citation_registry.assign_packet(result)
                        evidence_packets.append(global_packet)
                        tool_content = global_packet.to_tool_text()
                    elif hasattr(result, "to_tool_text"):
                        tool_content = result.to_tool_text()
                    else:
                        tool_content = str(result)
                    yield {
                        "type": "tool_result",
                        "tool": tool_name,
                        "summary": tool_content[:500],
                        "content": tool_content,
                    }
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": self._get_value(tool_call, "id", tool_name),
                            "name": tool_name,
                            "content": tool_content,
                        }
                    )

            answer_text=''.join(all_answer_parts)
            citations = self._filter_citations(answer_text, evidence_packets)
            if citations:
                answer_text, citations = self._renumber_citations(answer_text, citations)
                yield {"type": "citations", "data": citations}
                yield {"type": "answer_renumbered", "data": answer_text}
                yield {"type": "sources", "data": citations}
            full_text = answer_text or user_input
            genes = extract_gene_names(full_text)
            if genes:
                yield {"type": "genes_available", "genes": genes}
                try:
                    species = await asyncio.to_thread(extract_transgenic_species_with_llm, full_text)
                    if species:
                        yield {"type": "species_available", "species": species}
                except Exception:
                    pass
            yield {"type": "done"}
        except Exception as exc:
            logger.exception("Agent run failed")
            yield {"type": "error", "data": str(exc)}


__all__ = ["Agent"]
