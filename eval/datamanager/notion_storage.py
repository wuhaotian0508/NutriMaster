"""
Notion 存储 - 负责从 Notion 读取题目和写入结果
"""

import os
import re
from datetime import datetime, timezone
from typing import Any

from notion_client import Client as NotionClient


class NotionStorage:
    """Notion 数据库读写"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("NOTION_API_KEY")
        self.notion = NotionClient(auth=self.api_key)

    def load_questions(
        self,
        database_id: str,
        max_questions: int = 0,
        max_rubrics: int = 5,
        created_after: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """
        从 Notion 加载题目

        created_after: 只加载该时间点之后创建的 Notion page（按 page.created_time）。
        返回格式: [{"page_id": "...", "编号": 1, "正文": "...", "采分点": [...], ...}, ...]
        """
        questions = []
        created_after_utc = created_after.astimezone(timezone.utc) if created_after else None
        data_source_id = self._resolve_data_source_id(database_id)
        start_cursor = None

        while True:
            kwargs: dict[str, Any] = {"data_source_id": data_source_id, "page_size": 100}
            if start_cursor:
                kwargs["start_cursor"] = start_cursor

            response = self.notion.data_sources.query(**kwargs)
            for page in response["results"]:
                if created_after_utc and not self._is_page_created_after(page, created_after_utc):
                    continue
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
        data_source_id, db_properties = self._resolve_data_source(database_id)

        properties = {}
        self._add_if_exists(properties, db_properties, "题目编号", {"number": result.get("题目编号", 0)})
        self._add_if_exists(properties, db_properties, "Agent名称", {"select": {"name": result.get("Agent名称", "")}})
        self._add_if_exists(properties, db_properties, "版本", self._rich_text_prop(result.get("版本", "")))
        self._add_if_exists(properties, db_properties, "总分", {"number": result.get("总分", 0.0)})
        self._add_if_exists(properties, db_properties, "满分", {"number": result.get("满分", 0.0)})
        self._add_if_exists(properties, db_properties, "题目标题", self._rich_text_prop(result.get("题目标题", "")))
        if result.get("难度等级") and "难度等级" in db_properties:
            properties["难度等级"] = {"select": {"name": result["难度等级"]}}
        if result.get("领域大类") and "领域大类" in db_properties:
            properties["领域大类"] = {"select": {"name": result["领域大类"]}}
        if "Name" in db_properties:
            title = f"{result.get('Agent名称', '')} {result.get('版本', '')} 题目{result.get('题目编号', '')}"
            properties["Name"] = self._title_prop(title.strip())
        if "运行轮次" in db_properties:
            properties["运行轮次"] = {"select": {"name": str(result.get("运行轮次", "Run1"))}}
        if "得分率" in db_properties and result.get("满分"):
            properties["得分率"] = {"number": round((result.get("总分") or 0.0) / result["满分"], 4)}

        # 兼容新旧结果库字段名：旧库使用 Agent原始输出/评分原始输出。
        answer = result.get("答案") or result.get("Agent原始输出", "")
        answer_prop = self._first_existing_prop(db_properties, ["答案", "Agent原始输出"])
        if answer_prop:
            properties[answer_prop] = self._rich_text_prop(answer)

        judge_detail = result.get("评分详情") or result.get("评分原始输出", "")
        judge_detail_prop = self._first_existing_prop(db_properties, ["评分详情", "评分原始输出"])
        if judge_detail_prop:
            properties[judge_detail_prop] = self._rich_text_prop(judge_detail)

        if "Judge模型" in result or "评分模型" in result or os.getenv("JUDGE_MODEL"):
            judge_model = result.get("Judge模型") or result.get("评分模型") or self._short_model_name(os.getenv("JUDGE_MODEL", ""))
            if "Judge模型" in db_properties:
                properties["Judge模型"] = self._rich_text_prop(judge_model)
            elif "评分模型" in db_properties:
                properties["评分模型"] = {"select": {"name": judge_model}}
        if "耗时" in result and "耗时" in db_properties:
            properties["耗时"] = {"number": result["耗时"]}
        rubric_scores = self._extract_rubric_scores(result)
        for i in range(1, 6):
            key = f"采分点{i}-得分"
            score = result.get(key)
            if score is None:
                score = rubric_scores.get(i)
            if score is not None and key in db_properties:
                properties[key] = {"number": score}

        self._validate_result_properties(properties, db_properties)

        response = self.notion.pages.create(
            parent={"database_id": database_id},
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

                row_agent_name = self._get_select(props, "Agent名称")
                row_version = self._get_text(props, "版本")

                # 过滤
                if agent_name and row_agent_name != agent_name:
                    continue
                if version and row_version != version:
                    continue

                result = {
                    "题目编号": props.get("题目编号", {}).get("number"),
                    "Agent名称": row_agent_name,
                    "版本": row_version,
                    "答案": self._get_text(props, "答案") or self._get_text(props, "Agent原始输出"),
                    "总分": props.get("总分", {}).get("number"),
                    "满分": props.get("满分", {}).get("number"),
                    "评分详情": self._get_text(props, "评分详情") or self._get_text(props, "评分原始输出"),
                    "page_id": page.get("id"),
                    "时间戳": page.get("created_time"),
                }
                if props.get("Judge模型") or props.get("评分模型"):
                    result["Judge模型"] = self._get_text(props, "Judge模型") or self._get_select(props, "评分模型")
                if props.get("耗时", {}).get("number") is not None:
                    result["耗时"] = props["耗时"]["number"]
                results.append(result)

            if not response.get("has_more"):
                break
            start_cursor = response.get("next_cursor")

        return results

    def _resolve_data_source_id(self, database_id: str) -> str:
        data_source_id, _ = self._resolve_data_source(database_id)
        return data_source_id

    def _resolve_data_source(self, database_id: str) -> tuple[str, dict[str, Any]]:
        db = self.notion.databases.retrieve(database_id=database_id)
        data_source_id = self._data_source_id_from_database(db, database_id)
        data_source = self.notion.data_sources.retrieve(data_source_id=data_source_id)
        return data_source_id, data_source.get("properties", {})

    @staticmethod
    def _data_source_id_from_database(db: dict[str, Any], database_id: str) -> str:
        sources = db.get("data_sources") or []
        if not sources:
            raise RuntimeError(f"数据库 {database_id} 没有 data_source")
        return sources[0]["id"]

    @staticmethod
    def _validate_result_properties(
        properties: dict[str, Any],
        db_properties: dict[str, Any],
    ) -> None:
        required = ["题目编号", "Agent名称", "版本", "总分", "满分"]
        missing_schema = [name for name in required if name not in db_properties]
        if missing_schema:
            raise RuntimeError(f"结果库缺少必要字段: {', '.join(missing_schema)}")

        missing_payload = [name for name in required if name not in properties]
        if missing_payload:
            raise RuntimeError(f"Notion 写入 payload 缺少必要字段: {', '.join(missing_payload)}")

        if not any(name in properties for name in ("答案", "Agent原始输出")):
            raise RuntimeError("Notion 写入 payload 缺少答案字段（答案/Agent原始输出）")
        if not any(name in properties for name in ("评分详情", "评分原始输出")):
            raise RuntimeError("Notion 写入 payload 缺少评分字段（评分详情/评分原始输出）")

    @staticmethod
    def _add_if_exists(
        properties: dict[str, Any],
        db_properties: dict[str, Any],
        name: str,
        value: dict[str, Any],
    ) -> None:
        if name in db_properties:
            properties[name] = value

    @staticmethod
    def _first_existing_prop(db_properties: dict[str, Any], names: list[str]) -> str | None:
        for name in names:
            if name in db_properties:
                return name
        return None

    @staticmethod
    def _extract_rubric_scores(result: dict[str, Any]) -> dict[int, float]:
        detail = result.get("评分详情") or result.get("评分原始输出") or ""
        scores: dict[int, float] = {}
        for match in re.finditer(r"采分点\s*(\d+)\s*[:：]\s*(-?\d+(?:\.\d+)?)\s*分", detail):
            scores[int(match.group(1))] = float(match.group(2))
        return scores

    def _parse_question(self, page: dict[str, Any], max_rubrics: int) -> dict[str, Any]:
        props = page["properties"]
        rubrics = []
        created_time = page.get("created_time")
        last_edited_time = page.get("last_edited_time")

        for i in range(1, max_rubrics + 1):
            desc = self._get_text(props, f"采分点{i}-描述")
            score = props.get(f"采分点{i}-分值", {}).get("number")
            if desc:
                rubrics.append({"描述": desc, "满分": float(score or 0)})

        return {
            "page_id": page["id"],
            "编号": props.get("题目编号", {}).get("unique_id", {}).get("number", 0),
            "标题": self._get_title(props, "题目标题"),
            "正文": self._get_text(props, "题目正文"),
            "采分点": rubrics,
            "难度": self._get_select(props, "难度等级") or self._get_select(props, "难度"),
            "领域": self._get_select(props, "领域大类"),
            "小类": self._get_text(props, "领域小类"),
            "参考答案": self._get_text(props, "参考答案"),
            "标签": [tag["name"] for tag in props.get("标签", {}).get("multi_select", [])],
            "created_time": created_time,
            "last_edited_time": last_edited_time,
        }

    @staticmethod
    def _is_page_created_after(page: dict[str, Any], created_after_utc: datetime) -> bool:
        created_time = page.get("created_time")
        if not created_time:
            return False
        created_dt = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
        return created_dt >= created_after_utc

    @staticmethod
    def _get_text(props: dict[str, Any], key: str) -> str:
        rich_text = props.get(key, {}).get("rich_text", [])
        return "".join(block.get("plain_text", "") for block in rich_text)

    @staticmethod
    def _get_title(props: dict[str, Any], key: str) -> str:
        title = props.get(key, {}).get("title", [])
        return "".join(block.get("plain_text", "") for block in title)

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

    @staticmethod
    def _title_prop(text: str) -> dict[str, Any]:
        """构造 title 属性。"""
        return {"title": [{"text": {"content": text[:2000]}}]}

    @staticmethod
    def _short_model_name(model: str) -> str:
        return {
            "Vendor2/Claude-4.5-Sonnet": "Claude-4.5-Sonnet",
            "Vendor2/GPT-5.4": "GPT-5.4",
            "Vendor2/Gemini-3.1-pro": "Gemini-3.1-pro",
        }.get(model, model.split("/")[-1] if model else "")
