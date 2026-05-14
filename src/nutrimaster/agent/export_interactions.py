from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


DATASET_SCHEMA_VERSION = "dataset_export.v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 文件，将每一行解析为字典并返回列表。

    参数:
        path: JSONL 文件的路径。

    返回:
        list[dict[str, Any]]: 解析后的字典列表；文件不存在时返回空列表。
    """
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """将字典列表写入 JSONL 文件，每行一个 JSON 对象。

    参数:
        path: 输出文件路径，父目录不存在时会自动创建。
        rows: 可迭代的字典集合，每个字典将写为一行 JSON。

    返回:
        int: 实际写入的行数。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def load_capture_dir(input_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """从捕获目录加载交互记录和反馈数据。

    读取 interactions.jsonl 和 feedback.jsonl 文件，分别提取交互记录和用户反馈，
    并将反馈按 interaction_id 索引。

    参数:
        input_dir: 包含 interactions.jsonl 和 feedback.jsonl 的目录路径。

    返回:
        tuple: 二元组，第一个元素为交互记录列表，第二个元素为按 interaction_id
               索引的反馈字典。
    """
    interactions = [
        row
        for row in read_jsonl(input_dir / "interactions.jsonl")
        if row.get("record_type") == "interaction"
    ]
    feedback_rows = [
        row
        for row in read_jsonl(input_dir / "feedback.jsonl")
        if row.get("record_type") == "feedback"
    ]
    feedback_by_interaction: dict[str, dict[str, Any]] = {}
    for row in feedback_rows:
        interaction_id = row.get("interaction_id")
        if interaction_id:
            feedback_by_interaction[interaction_id] = row
    return interactions, feedback_by_interaction


def sft_rows(
    interactions: list[dict[str, Any]],
    feedback_by_interaction: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """将交互记录转换为监督微调（SFT）训练数据格式。

    筛选已完成且有回答的交互记录，将对话消息和助手回复组装为 SFT 训练样本，
    并附加元数据（会话信息、模型、工具使用情况、反馈等）。

    参数:
        interactions: 交互记录列表。
        feedback_by_interaction: 按 interaction_id 索引的用户反馈字典。

    返回:
        list[dict[str, Any]]: SFT 格式的训练数据列表。
    """
    rows = []
    for record in interactions:
        final = record.get("final") or {}
        answer = final.get("answer_text") or ""
        messages = list(record.get("messages") or [])
        if record.get("status") != "completed" or not answer or not messages:
            continue
        messages.append({"role": "assistant", "content": answer})
        interaction_id = record.get("interaction_id", "")
        feedback = feedback_by_interaction.get(interaction_id)
        rows.append(
            {
                "schema_version": DATASET_SCHEMA_VERSION,
                "task": "sft",
                "id": interaction_id,
                "source": "production",
                "messages": messages,
                "metadata": {
                    "session_id": record.get("session_id", ""),
                    "turn_id": record.get("turn_id", ""),
                    "created_at": record.get("created_at", ""),
                    "model_id": (record.get("request") or {}).get("model_id", ""),
                    "use_personal": (record.get("request") or {}).get("use_personal", False),
                    "use_depth": (record.get("request") or {}).get("use_depth", False),
                    "tools_used": final.get("tools_used", []),
                    "citations": final.get("citations", []),
                    "feedback": feedback.get("rating") if feedback else "",
                },
            }
        )
    return rows


def preference_rows(
    interactions: list[dict[str, Any]],
    feedback_by_interaction: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """将交互记录转换为偏好对齐（preference/DPO）训练数据格式。

    根据用户反馈的 up/down 评分，将相同查询的正面和负面回答配对，
    生成 chosen/rejected 偏好对训练样本。

    参数:
        interactions: 交互记录列表。
        feedback_by_interaction: 按 interaction_id 索引的用户反馈字典。

    返回:
        list[dict[str, Any]]: 偏好对格式的训练数据列表。
    """
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"up": [], "down": []})
    for record in interactions:
        final = record.get("final") or {}
        answer = final.get("answer_text") or ""
        feedback = feedback_by_interaction.get(record.get("interaction_id", ""))
        if record.get("status") != "completed" or not answer or not feedback:
            continue
        rating = feedback.get("rating")
        if rating not in {"up", "down"}:
            continue
        query = ((record.get("request") or {}).get("query") or "").strip().lower()
        if not query:
            continue
        grouped[query][rating].append(record)

    rows = []
    for query, bucket in grouped.items():
        for chosen in bucket["up"]:
            for rejected in bucket["down"]:
                rows.append(
                    {
                        "schema_version": DATASET_SCHEMA_VERSION,
                        "task": "preference",
                        "id": f"{chosen.get('interaction_id')}__vs__{rejected.get('interaction_id')}",
                        "source": "production_feedback",
                        "prompt": chosen.get("messages") or [],
                        "chosen": (chosen.get("final") or {}).get("answer_text", ""),
                        "rejected": (rejected.get("final") or {}).get("answer_text", ""),
                        "metadata": {
                            "query": query,
                            "chosen_interaction_id": chosen.get("interaction_id", ""),
                            "rejected_interaction_id": rejected.get("interaction_id", ""),
                        },
                    }
                )
    return rows


def raw_rows(
    interactions: list[dict[str, Any]],
    feedback_by_interaction: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """将交互记录导出为原始格式，保留完整的交互数据并附加反馈信息。

    参数:
        interactions: 交互记录列表。
        feedback_by_interaction: 按 interaction_id 索引的用户反馈字典。

    返回:
        list[dict[str, Any]]: 包含完整交互数据和反馈的原始记录列表。
    """
    rows = []
    for record in interactions:
        next_record = dict(record)
        feedback = feedback_by_interaction.get(record.get("interaction_id", ""))
        if feedback:
            next_record["feedback"] = feedback
        rows.append(next_record)
    return rows


def build_rows(
    export_format: str,
    interactions: list[dict[str, Any]],
    feedback_by_interaction: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """根据指定的导出格式构建训练数据行。

    参数:
        export_format: 导出格式，支持 "sft"、"preference" 和 "raw"。
        interactions: 交互记录列表。
        feedback_by_interaction: 按 interaction_id 索引的用户反馈字典。

    返回:
        list[dict[str, Any]]: 对应格式的训练数据列表。

    异常:
        ValueError: 当 export_format 不是支持的格式时抛出。
    """
    if export_format == "sft":
        return sft_rows(interactions, feedback_by_interaction)
    if export_format == "preference":
        return preference_rows(interactions, feedback_by_interaction)
    if export_format == "raw":
        return raw_rows(interactions, feedback_by_interaction)
    raise ValueError(f"Unsupported format: {export_format}")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    返回:
        argparse.Namespace: 包含 input_dir、output 和 format 的命令行参数对象。
    """
    parser = argparse.ArgumentParser(description="Export NutriMaster interaction captures into training datasets.")
    parser.add_argument("--input-dir", type=Path, default=Path("data/interactions"))
    parser.add_argument("--output", type=Path, default=Path("data/interactions/dataset_sft.jsonl"))
    parser.add_argument("--format", choices=["sft", "preference", "raw"], default="sft")
    return parser.parse_args()


def main() -> None:
    """交互数据导出的主入口函数。

    解析命令行参数，加载交互记录和反馈数据，按指定格式构建训练数据并写入输出文件。
    """
    args = parse_args()
    interactions, feedback_by_interaction = load_capture_dir(args.input_dir)
    rows = build_rows(args.format, interactions, feedback_by_interaction)
    count = write_jsonl(args.output, rows)
    print(f"Exported {count} {args.format} rows to {args.output}")


if __name__ == "__main__":
    main()
