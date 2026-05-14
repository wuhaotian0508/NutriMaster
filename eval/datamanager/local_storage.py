"""
本地文件存储 - 负责读写本地 JSONL 文件
"""

import json
from pathlib import Path
from typing import Any


class LocalStorage:
    """本地 JSONL 文件读写"""

    @staticmethod
    def save_questions(filepath: str, questions: list[dict[str, Any]]):
        """
        保存题目到本地 JSONL 文件

        questions 格式: [{"编号": 1, "正文": "...", "采分点": [...], ...}, ...]
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for question in questions:
                f.write(json.dumps(question, ensure_ascii=False) + "\n")

    @staticmethod
    def load_questions(filepath: str, max_questions: int = 0) -> list[dict[str, Any]]:
        """
        从本地 JSONL 文件加载题目

        返回格式: [{"编号": 1, "正文": "...", "采分点": [...], ...}, ...]
        """
        questions = []
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("正文"):
                    questions.append(record)
                    if max_questions > 0 and len(questions) >= max_questions:
                        break
        return questions

    @staticmethod
    def save_results(filepath: str, results: list[dict[str, Any]]):
        """
        保存评测结果到本地 JSONL 文件

        results 格式: [{"题目编号": 1, "答案": "...", "总分": 8.5, ...}, ...]
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for result in results:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")

    @staticmethod
    def load_results(filepath: str) -> list[dict[str, Any]]:
        """
        从本地 JSONL 文件加载评测结果

        返回格式: [{"题目编号": 1, "总分": 8.5, "满分": 10.0, ...}, ...]
        """
        results = []
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                results.append(json.loads(line))
        return results
