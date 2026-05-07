"""
LLM Judge - 使用 LLM 对答案进行评分
"""

import json
import os
import re
from typing import Any

from openai import AsyncOpenAI


class LLMJudge:
    """LLM 评分器"""

    def __init__(self, model: str, base_url: str = None, api_key: str = None):
        self.model = model
        self.client = AsyncOpenAI(
            base_url=base_url or os.getenv("LLM_BASE_URL", "https://api.gpugeek.com/v1"),
            api_key=api_key or os.getenv("LLM_API_KEY"),
        )

    async def judge(self, question: str, answer: str, rubrics: list[dict[str, Any]]) -> dict[str, Any]:
        """
        评分

        返回: {
            "ok": True/False,
            "总分": 8.5,
            "满分": 10.0,
            "评分详情": "...",
            "error": "错误信息"
        }
        """
        if not rubrics:
            return {"ok": False, "总分": 0.0, "满分": 0.0, "评分详情": "", "error": "缺少采分点"}

        max_score = sum(r.get("满分", 0) for r in rubrics)

        if not answer or not answer.strip():
            return {"ok": True, "总分": 0.0, "满分": max_score, "评分详情": "答案为空", "error": None}

        try:
            # 构建评分提示词
            prompt = self._build_prompt(question, answer, rubrics)

            # 调用 LLM 评分
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )

            judge_output = response.choices[0].message.content or ""

            # 解析评分结果
            score, details = self._parse_output(judge_output)

            return {
                "ok": True,
                "总分": score,
                "满分": max_score,
                "评分详情": details,
                "error": None,
            }

        except Exception as e:
            return {"ok": False, "总分": 0.0, "满分": max_score, "评分详情": "", "error": str(e)}

    def _build_prompt(self, question: str, answer: str, rubrics: list[dict[str, Any]]) -> str:
        """构建评分提示词"""
        rubric_text = "\n".join(
            f"{i}. {r['描述']} (满分: {r['满分']})" for i, r in enumerate(rubrics, 1)
        )

        return f"""你是一位专业的营养代谢基因知识评委。请根据以下采分点对答案进行评分。

**题目：**
{question}

**采分点：**
{rubric_text}

**学生答案：**
{answer}

**评分要求：**
1. 对每个采分点，判断答案是否覆盖该知识点
2. 根据答案的准确性和完整性给出得分（0 到满分之间）
3. 提供简要的评分理由

**输出格式（JSON）：**
```json
{{
  "采分点评分": [
    {{"编号": 1, "得分": 5.0, "理由": "..."}},
    {{"编号": 2, "得分": 3.0, "理由": "..."}}
  ],
  "总分": 8.0,
  "总评": "..."
}}
```

请严格按照 JSON 格式输出，不要添加其他内容。"""

    def _parse_output(self, judge_output: str) -> tuple[float, str]:
        """
        解析 LLM 评分输出

        返回: (总分, 评分详情文本)
        """
        # 提取 JSON
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", judge_output, re.DOTALL)
        if not json_match:
            json_match = re.search(r"\{.*?\}", judge_output, re.DOTALL)

        if not json_match:
            return 0.0, f"无法解析评分结果：{judge_output[:200]}"

        try:
            result = json.loads(json_match.group(1) if json_match.groups() else json_match.group(0))
            score = float(result.get("总分", 0))

            # 格式化评分详情
            details_lines = [f"总评: {result.get('总评', '')}"]
            for rubric_score in result.get("采分点评分", []):
                details_lines.append(
                    f"采分点{rubric_score['编号']}: {rubric_score['得分']}分 - {rubric_score['理由']}"
                )

            return score, "\n".join(details_lines)

        except (json.JSONDecodeError, ValueError) as e:
            return 0.0, f"JSON 解析失败: {e}"

    async def close(self):
        """关闭客户端"""
        await self.client.close()
