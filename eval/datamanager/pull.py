from datetime import datetime
from pathlib import Path
from typing import Any

from eval.configs import (
    DEFAULT_QUESTIONS_FILE,
    LEGACY_QUESTIONS_FILE,
    LOCAL_TIMEZONE,
    QUESTION_DB_ID,
    QUESTIONS_DIR,
)
from eval.datamanager.local_storage import LocalStorage
from eval.datamanager.notion_storage import NotionStorage


def parse_local_datetime(value: str) -> datetime:
    if "T" in value:
        dt = datetime.fromisoformat(value)
    else:
        dt = datetime.strptime(value, "%Y-%m-%d")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TIMEZONE)
    return dt


def safe_file_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value).strip("-")


def default_questions_output(after: str | None) -> Path:
    if after:
        return QUESTIONS_DIR / f"questions_after_{safe_file_part(after)}.jsonl"
    timestamp = datetime.now(LOCAL_TIMEZONE).strftime("%Y%m%d_%H%M%S")
    return QUESTIONS_DIR / f"questions_{timestamp}.jsonl"


def resolve_questions_file(path: str | None) -> Path:
    if path:
        return Path(path)
    if DEFAULT_QUESTIONS_FILE.exists():
        return DEFAULT_QUESTIONS_FILE
    return LEGACY_QUESTIONS_FILE


def filter_questions_by_created_after(
    questions: list[dict[str, Any]],
    created_after: datetime | None,
) -> list[dict[str, Any]]:
    """按本地题目里的 Notion created_time 过滤。"""
    if created_after is None:
        return questions

    threshold = created_after.astimezone(LOCAL_TIMEZONE)
    filtered = []
    for question in questions:
        created_time = question.get("created_time")
        if not created_time:
            continue
        created_dt = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
        if created_dt.astimezone(LOCAL_TIMEZONE) >= threshold:
            filtered.append(question)
    return filtered


def pull_questions(after: str | None = None, max_questions: int = 0, output: str | None = None) -> Path:
    created_after = parse_local_datetime(after) if after else None
    if created_after:
        print(f"题目创建时间过滤: created_time >= {created_after.isoformat()}")

    storage = NotionStorage()

    questions = storage.load_questions(
        database_id=QUESTION_DB_ID,
        max_questions=max_questions,
        created_after=created_after,
    )

    output_path = Path(output) if output else default_questions_output(after)
    LocalStorage.save_questions(str(output_path), questions)
    LocalStorage.save_questions(str(DEFAULT_QUESTIONS_FILE), questions)

    print(f"✓ 已下载题目: {len(questions)} 道")
    print(f"✓ 已保存: {output_path}")
    print(f"✓ 已更新 latest: {DEFAULT_QUESTIONS_FILE}")
    return output_path


def load_local_questions(
    questions_file: str | None,
    max_questions: int = 0,
    question_created_after: str | None = None,
) -> tuple[list[dict[str, Any]], Path]:
    path = resolve_questions_file(questions_file)
    if not path.exists():
        raise FileNotFoundError(f"本地题目文件不存在: {path}")

    created_after = parse_local_datetime(question_created_after) if question_created_after else None
    questions = LocalStorage.load_questions(str(path), max_questions=max_questions)
    return filter_questions_by_created_after(questions, created_after), path
