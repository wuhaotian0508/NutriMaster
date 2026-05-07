"""
LLM Agent - 通用 LLM 基线模型
"""

import os
from typing import Any

from openai import AsyncOpenAI


class LLMAgent:
    """通用 LLM Agent"""

    def __init__(self, model_id: str, base_url: str, api_key: str):
        self.model_id = model_id
        self.name = model_id.split("/")[-1]
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def answer(self, question: str) -> dict[str, Any]:
        """
        回答问题

        返回: {"ok": True/False, "output": "答案", "error": "错误信息"}
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个植物营养科学领域的专家级AI助手。"
                            "请根据你的知识，尽可能全面、准确、有理有据地回答以下问题。"
                            "如果涉及基因、通路、文献，请提供具体的标识符（如Gene ID、KEGG编号、PMID等）。"
                        ),
                    },
                    {"role": "user", "content": question},
                ],
                max_tokens=16384,
                temperature=0.3,
            )

            output = response.choices[0].message.content or ""
            if not output.strip():
                return {"ok": False, "error": "Agent 返回空内容", "output": ""}

            return {"ok": True, "output": output, "error": None}

        except Exception as e:
            return {"ok": False, "error": str(e), "output": ""}

    async def close(self):
        """关闭客户端"""
        await self.client.close()
