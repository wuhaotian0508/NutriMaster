"""
Notion 存储 - 负责从 Notion 读取题目和写入结果
"""

import os
from typing import Any

from notion_client import Client as NotionClient


class NotionStorage:
    """Notion 数据库读写"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("NOTION_API_KEY")
        self.notion = NotionClient(auth=self.api_key)

    def load_questions(self, database_id: str, max_questions: int = 0, max_rubrics: int = 5) -> list[dict[str, Any]]:
        """
        从 Notion 加载题目

        返回格式: [{"page_id": "...", "编号": 1, "正文": "...", "采分点": [...], ...}, ...]
        """
        questions = []
        data_source_id = self._resolve_data_source_id(database_id)
        start_cursor = None

        while True:
            kwargs: dict[str, Any] = {"data_source_id": data_source_id, "page_size": 100}
            if start_cursor:
                kwargs["start_cursor"] = start_cursor

            response = self.notion.data_sources.query(**kwargs)
            for page in response["results"]:
                question = self._parse_question(page, max_rubrics)
                if question["正文"]:
                    questions.append(question)
                    if max_questions > 0 and len(questions) >= max_questions:
                        return questions

            if not response.get("has_more"):
                break
            start_cursor = response.get("next_cursor")

        return questions

    def save_result(self, database_id: str, result: dict[str, Any]) -> str:
        """
        保存评测结果到 Notion

        result 格式: {
            "题目编号": 1,
            "Agent名称": "...",
            "版本": "v3",
            "答案": "...",
            "总分": 8.5,
            "满分": 10.0,
            "评分详情": "...",
            ...
        }

        返回: 创建的 page_id
        """
        data_source_id = self._resolve_data_source_id(database_id)

        properties = {
            "题目编号": {"number": result.get("题目编号", 0)},
            "Agent名称": {"select": {"name": result.get("Agent名称", "")}},
            "版本": self._rich_text_prop(result.get("版本", "")),
            "答案": self._rich_text_prop(result.get("答案", "")),
            "总分": {"number": result.get("总分", 0.0)},
            "满分": {"number": result.get("满分", 0.0)},
            "评分详情": self._rich_text_prop(result.get("评分详情", "")),
        }

        # 可选字段
        if "Judge模型" in result:
            properties["Judge模型"] = self._rich_text_prop(result["Judge模型"])
        if "耗时" in result:
            properties["耗时"] = {"number": result["耗时"]}

        response = self.notion.pages.create(
            parent={"type": "data_source_id", "data_source_id": data_source_id},
            properties=properties,
        )

        return response["id"]

    def load_results(self, database_id: str, agent_name: str = None, version: str = None) -> list[dict[str, Any]]:
        """
        从 Notion 加载评测结果

        返回格式: [{"题目编号": 1, "总分": 8.5, "满分": 10.0, ...}, ...]
        """
        results = []
        data_source_id = self._resolve_data_source_id(database_id)
        start_cursor = None

        while True:
            kwargs: dict[str, Any] = {"data_source_id": data_source_id, "page_size": 100}
            if start_cursor:
                kwargs["start_cursor"] = start_cursor

            response = self.notion.data_sources.query(**kwargs)
            for page in response["results"]:
                props = page["properties"]

                # 过滤
                if agent_name and self._get_select(props, "Agent名称") != agent_name:
                    continue
                if version and self._get_text(props, "版本") != version:
                    continue

                results.append({
                    "题目编号": props.get("题目编号", {}).get("number"),
                    "总分": props.get("总分", {}).get("number"),
                    "满分": props.get("满分", {}).get("number"),
                    "Agent名称": self._get_select(props, "Agent名称"),
                    "版本": self._get_text(props, "版本"),
                })

            if not response.get("has_more"):
                break
            start_cursor = response.get("next_cursor")

        return results

    def _resolve_data_source_id(self, database_id: str) -> str:
        db = self.notion.databases.retrieve(database_id=database_id)
        sources = db.get("data_sources") or []
        if not sources:
            raise RuntimeError(f"数据库 {database_id} 没有 data_source")
        return sources[0]["id"]

    def _parse_question(self, page: dict[str, Any], max_rubrics: int) -> dict[str, Any]:
        props = page["properties"]
        rubrics = []

        for i in range(1, max_rubrics + 1):
            desc = self._get_text(props, f"采分点{i}-描述")
            score = props.get(f"采分点{i}-分值", {}).get("number")
            if desc:
                rubrics.append({"描述": desc, "满分": float(score or 0)})

        return {
            "page_id": page["id"],
            "编号": props.get("题目编号", {}).get("unique_id", {}).get("number", 0),
            "正文": self._get_text(props, "题目正文"),
            "采分点": rubrics,
            "难度": self._get_select(props, "难度"),
            "标签": [tag["name"] for tag in props.get("标签", {}).get("multi_select", [])],
        }

    @staticmethod
    def _get_text(props: dict[str, Any], key: str) -> str:
        rich_text = props.get(key, {}).get("rich_text", [])
        return "".join(block.get("plain_text", "") for block in rich_text)

    @staticmethod
    def _get_select(props: dict[str, Any], key: str) -> str:
        selected = props.get(key, {}).get("select")
        return selected["name"] if selected else ""

    @staticmethod
    def _rich_text_prop(text: str) -> dict[str, Any]:
        """构造 rich_text 属性（处理长文本截断）"""
        CHUNK_SIZE = 1800
        MAX_BLOCKS = 100

        if not text:
            return {"rich_text": [{"text": {"content": ""}}]}

        chunks = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
        if len(chunks) > MAX_BLOCKS:
            chunks = chunks[:MAX_BLOCKS]
            chunks[-1] = chunks[-1][:1700] + "\n\n[TRUNCATED]"

        chunks = [chunk[:2000] for chunk in chunks]
        return {"rich_text": [{"text": {"content": chunk}} for chunk in chunks]}
