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
        self.stream = os.getenv("JUDGE_STREAM", "0").lower() in {"1", "true", "yes"}
        self.client = AsyncOpenAI(
            base_url=base_url or os.getenv("LLM_BASE_URL", "https://api.gpugeek.com/v1"),
            api_key=api_key or os.getenv("LLM_API_KEY"),
        )

    async def judge(
        self,
        question: str,
        answer: str,
        rubrics: list[dict[str, Any]],
        reference_answer: str = "",
    ) -> dict[str, Any]:
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
            prompt = self._build_prompt(question, answer, rubrics, reference_answer)

            # 调用 LLM 评分
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=16384,
                temperature=0.1,
                stream=self.stream,
            )

            if self.stream:
                chunks = []
                async for chunk in response:
                    if chunk.choices:
                        chunks.append(chunk.choices[0].delta.content or "")
                judge_output = "".join(chunks)
            else:
                judge_output = response.choices[0].message.content or ""

            # 解析评分结果
            score, details, rubric_scores = self._parse_output(judge_output, rubrics)

            return {
                "ok": True,
                "总分": score,
                "满分": max_score,
                "评分详情": details,
                "采分点得分": rubric_scores,
                "error": None,
            }

        except Exception as e:
            return {"ok": False, "总分": 0.0, "满分": max_score, "评分详情": "", "采分点得分": [], "error": str(e)}

    def _build_prompt(
        self,
        question: str,
        answer: str,
        rubrics: list[dict[str, Any]],
        reference_answer: str = "",
    ) -> str:
        """构建评分提示词"""
        rubric_text = "\n".join(
            f"{i}. {r['描述']} (满分: {r['满分']})" for i, r in enumerate(rubrics, 1)
        )

        return f"""你是一位专业的营养代谢基因知识评委。请根据以下采分点对答案进行评分。

**题目：**
{question}

**参考答案：**
{reference_answer or "（无）"}

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

    def _parse_output(self, judge_output: str, rubrics: list[dict[str, Any]]) -> tuple[float, str, list[float]]:
        """
        解析 LLM 评分输出

        返回: (总分, 评分详情文本)
        """
        # 提取完整 JSON；评分结果内有嵌套对象，不能用非贪婪括号正则。
        json_match = re.search(r"```json\s*(.*?)\s*```", judge_output, re.DOTALL)
        if json_match:
            json_text = json_match.group(1).strip()
        else:
            start = judge_output.find("{")
            end = judge_output.rfind("}")
            json_text = judge_output[start : end + 1].strip() if start != -1 and end != -1 and end > start else ""

        if not json_text:
            raise ValueError(f"无法解析评分结果：{judge_output[:200]}")

        try:
            result = json.loads(json_text)
            rubric_scores = []
            for i, (rubric_score, rubric) in enumerate(zip(result.get("采分点评分", []), rubrics), start=1):
                raw_score = float(rubric_score.get("得分", 0))
                max_score = float(rubric.get("满分", 0))
                rubric_scores.append(min(max(raw_score, 0.0), max_score))
                rubric_score.setdefault("编号", i)

            if len(rubric_scores) != len(rubrics):
                raise ValueError(f"Judge 返回 {len(rubric_scores)} 个采分点分数，期望 {len(rubrics)} 个")

            score = round(sum(rubric_scores), 4)

            # 格式化评分详情
            details_lines = [f"总评: {result.get('总评', '')}"]
            for rubric_score in result.get("采分点评分", []):
                details_lines.append(
                    f"采分点{rubric_score['编号']}: {rubric_score['得分']}分 - {rubric_score['理由']}"
                )

            return score, "\n".join(details_lines), rubric_scores

        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"JSON 解析失败: {e}") from e

    async def close(self):
        """关闭客户端"""
        await self.client.close()
