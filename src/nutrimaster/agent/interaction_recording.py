from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from nutrimaster.config.settings import Settings

PrivacyLevel = Literal["minimal", "standard", "full"]

SCHEMA_VERSION = "interaction.v1"
_REDACTED_PERSONAL_TOOL_RESULT = "[redacted: personal library tool result]"


def _utc_now() -> str:
    """获取当前 UTC 时间的 ISO 格式字符串。

    返回:
        str: 当前 UTC 时间的 ISO 8601 格式字符串。
    """
    return datetime.now(timezone.utc).isoformat()


def _as_bool(value: str | None, default: bool = False) -> bool:
    """将字符串值转换为布尔值。

    参数:
        value: 待转换的字符串值，支持 "1"、"true"、"yes"、"on"（不区分大小写）。
        default: 当 value 为 None 或空字符串时返回的默认值。

    返回:
        bool: 转换后的布尔值。
    """
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    """将字符串值转换为整数。

    参数:
        value: 待转换的字符串值。
        default: 当 value 为 None 或空字符串时返回的默认值。

    返回:
        int: 转换后的整数值。
    """
    if value in (None, ""):
        return default
    return int(value)


def _truncate(value: Any, limit: int) -> Any:
    """递归截断字符串、列表或字典中超出长度限制的文本内容。

    参数:
        value: 待截断的值，支持 str、list、dict 类型的递归处理。
        limit: 字符串最大允许长度，超出部分将被替换为 "[truncated]" 标记。

    返回:
        Any: 截断处理后的值，保持原始数据类型。
    """
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "\n...[truncated]"
    if isinstance(value, list):
        return [_truncate(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: _truncate(item, limit) for key, item in value.items()}
    return value


def _contains_personal_source(value: Any) -> bool:
    """递归检查值中是否包含个人知识库来源的标识。

    检测字符串中是否包含 "source_type': 'personal'" 等个人库来源标记。

    参数:
        value: 待检查的值，支持 str、list、dict 类型的递归检查。

    返回:
        bool: 如果包含个人来源标识返回 True，否则返回 False。
    """
    if isinstance(value, dict):
        return any(_contains_personal_source(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_personal_source(item) for item in value)
    if not isinstance(value, str):
        return False
    text = value.lower()
    return "source_type': 'personal'" in text or '"source_type": "personal"' in text or "来源: personal" in text


@dataclass(frozen=True)
class InteractionCapturePolicy:
    """交互记录捕获策略的配置数据类。

    定义了交互记录的启用状态、隐私级别、存储路径等策略参数，
    控制交互数据的采集范围和脱敏程度。

    属性:
        enabled: 是否启用交互记录捕获。
        require_consent: 是否要求用户同意后才进行捕获。
        privacy_level: 隐私级别，可选 "minimal"、"standard"、"full"。
        storage_dir: 交互记录存储目录。
        user_hash_salt: 用户 ID 哈希的盐值。
        max_text_chars: 文本最大字符数限制。
        max_event_content_chars: 事件内容最大字符数限制。
        include_personal_content: 是否包含个人库内容。
        include_system_prompt: 是否包含系统提示词。
    """
    enabled: bool
    require_consent: bool
    privacy_level: PrivacyLevel
    storage_dir: Path
    user_hash_salt: str = ""
    max_text_chars: int = 12000
    max_event_content_chars: int = 4000
    include_personal_content: bool = False
    include_system_prompt: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> "InteractionCapturePolicy":
        """从应用配置和环境变量创建捕获策略实例。

        从环境变量中读取各项配置（如 NUTRIMASTER_INTERACTION_CAPTURE_ENABLED、
        NUTRIMASTER_INTERACTION_PRIVACY_LEVEL 等），未设置时使用默认值。

        参数:
            settings: 应用全局配置对象，提供项目根目录和服务密钥等信息。

        返回:
            InteractionCapturePolicy: 根据配置创建的策略实例。
        """
        env = os.environ
        storage_dir = Path(
            env.get(
                "NUTRIMASTER_INTERACTION_CAPTURE_DIR",
                settings.project_root / "data" / "interactions",
            )
        )
        privacy_level = env.get("NUTRIMASTER_INTERACTION_PRIVACY_LEVEL", "standard").strip().lower()
        if privacy_level not in {"minimal", "standard", "full"}:
            privacy_level = "standard"
        return cls(
            enabled=_as_bool(env.get("NUTRIMASTER_INTERACTION_CAPTURE_ENABLED"), True),
            require_consent=_as_bool(env.get("NUTRIMASTER_INTERACTION_CAPTURE_REQUIRE_CONSENT"), False),
            privacy_level=privacy_level,  # type: ignore[arg-type]
            storage_dir=storage_dir,
            user_hash_salt=env.get("NUTRIMASTER_INTERACTION_USER_HASH_SALT", settings.supabase_service_role_key),
            max_text_chars=_as_int(env.get("NUTRIMASTER_INTERACTION_MAX_TEXT_CHARS"), 12000),
            max_event_content_chars=_as_int(env.get("NUTRIMASTER_INTERACTION_MAX_EVENT_CHARS"), 4000),
            include_personal_content=_as_bool(env.get("NUTRIMASTER_INTERACTION_INCLUDE_PERSONAL_CONTENT"), False),
            include_system_prompt=_as_bool(env.get("NUTRIMASTER_INTERACTION_INCLUDE_SYSTEM_PROMPT"), True),
        )

    def public_config(self) -> dict[str, Any]:
        """返回可公开的策略配置信息，用于前端展示。

        返回:
            dict[str, Any]: 包含 enabled、require_consent 和 privacy_level 的字典。
        """
        return {
            "enabled": self.enabled,
            "require_consent": self.require_consent,
            "privacy_level": self.privacy_level,
        }


class InteractionRecorder:
    """交互记录器，负责管理交互会话的创建、反馈记录和数据持久化。"""

    def __init__(self, policy: InteractionCapturePolicy):
        """初始化交互记录器。

        参数:
            policy: 交互记录捕获策略配置。
        """
        self.policy = policy
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "InteractionRecorder":
        """从应用配置创建交互记录器实例的工厂方法。

        参数:
            settings: 应用全局配置对象。

        返回:
            InteractionRecorder: 新创建的交互记录器实例。
        """
        return cls(InteractionCapturePolicy.from_settings(settings))

    def start(
        self,
        *,
        user_id: str | None,
        session_id: str | None,
        client_turn_id: str | None,
        query: str,
        model_id: str,
        history: list[dict],
        initial_messages: list[dict],
        use_personal: bool,
        use_depth: bool,
        capture_consent: bool,
    ) -> "InteractionRecordingSession":
        """启动一个新的交互记录会话。

        创建交互记录的初始数据结构，包括用户信息、请求参数、同意状态等，
        并返回一个 InteractionRecordingSession 实例用于后续事件捕获。

        参数:
            user_id: 用户标识符。
            session_id: 会话标识符。
            client_turn_id: 客户端提供的对话轮次 ID。
            query: 用户查询文本。
            model_id: 使用的 LLM 模型标识符。
            history: 对话历史消息列表。
            initial_messages: 初始消息列表（含系统提示词等）。
            use_personal: 是否启用个人知识库。
            use_depth: 是否启用深度搜索。
            capture_consent: 用户是否同意数据捕获。

        返回:
            InteractionRecordingSession: 新创建的交互记录会话。
        """
        interaction_id = str(uuid.uuid4())
        turn_id = client_turn_id or str(uuid.uuid4())
        active = self.policy.enabled and (capture_consent or not self.policy.require_consent)
        record = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "interaction",
            "interaction_id": interaction_id,
            "session_id": session_id or "",
            "turn_id": turn_id,
            "created_at": _utc_now(),
            "completed_at": None,
            "status": "running" if active else "skipped",
            "user": self._user_payload(user_id),
            "consent": {
                "granted": bool(capture_consent) or not self.policy.require_consent,
                "required": self.policy.require_consent,
                "privacy_level": self.policy.privacy_level,
            },
            "request": {
                "query": self._sanitize_text(query),
                "model_id": model_id or "",
                "use_personal": bool(use_personal),
                "use_depth": bool(use_depth),
                "history_count": len(history or []),
            },
            "messages": self._sanitize_messages(initial_messages, use_personal=use_personal),
            "events": [],
            "final": {
                "answer_text": "",
                "citations": [],
                "genes": [],
                "tools_used": [],
                "error": "",
            },
        }
        return InteractionRecordingSession(
            recorder=self,
            active=active,
            record=record,
            use_personal=use_personal,
        )

    def record_feedback(
        self,
        *,
        user_id: str | None,
        interaction_id: str,
        session_id: str | None,
        turn_id: str | None,
        rating: str,
        comment: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """记录用户对某次交互的反馈评价。

        创建反馈记录并持久化到 feedback.jsonl 文件。

        参数:
            user_id: 用户标识符。
            interaction_id: 关联的交互记录 ID。
            session_id: 会话标识符。
            turn_id: 对话轮次 ID。
            rating: 评分，如 "up" 或 "down"。
            comment: 用户的文字反馈评论。
            tags: 反馈标签列表。

        返回:
            dict[str, Any]: 完整的反馈记录字典。
        """
        payload = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "feedback",
            "feedback_id": str(uuid.uuid4()),
            "interaction_id": interaction_id,
            "session_id": session_id or "",
            "turn_id": turn_id or "",
            "created_at": _utc_now(),
            "user": self._user_payload(user_id),
            "rating": rating,
            "comment": self._sanitize_text(comment),
            "tags": tags or [],
        }
        if self.policy.enabled:
            self._append_jsonl("feedback.jsonl", payload)
        return payload

    def _finish(self, record: dict[str, Any]) -> None:
        """将完成的交互记录写入 interactions.jsonl 文件。

        参数:
            record: 完整的交互记录字典。
        """
        self._append_jsonl("interactions.jsonl", record)

    def _append_jsonl(self, filename: str, payload: dict[str, Any]) -> None:
        """以追加模式将一条 JSON 记录写入 JSONL 文件（线程安全）。

        参数:
            filename: 目标文件名（相对于 storage_dir）。
            payload: 要写入的 JSON 数据字典。
        """
        self.policy.storage_dir.mkdir(parents=True, exist_ok=True)
        path = self.policy.storage_dir / filename
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")

    def _user_payload(self, user_id: str | None) -> dict[str, str]:
        """根据隐私策略生成用户标识载荷。

        在 minimal 和 standard 级别下仅包含哈希后的用户 ID，
        在 full 级别下同时包含原始用户 ID。

        参数:
            user_id: 原始用户标识符。

        返回:
            dict[str, str]: 包含 user_hash（及可能的 user_id）的用户载荷。
        """
        if not user_id:
            return {"user_hash": ""}
        digest = hashlib.sha256(f"{self.policy.user_hash_salt}:{user_id}".encode("utf-8")).hexdigest()
        payload = {"user_hash": digest}
        if self.policy.privacy_level == "full":
            payload["user_id"] = user_id
        return payload

    def _sanitize_messages(self, messages: list[dict], *, use_personal: bool) -> list[dict]:
        """根据隐私策略对消息列表进行脱敏处理。

        在 minimal 级别下仅保留角色和内容长度；在 standard/full 级别下，
        根据配置对系统提示词和个人库工具结果进行脱敏或截断。

        参数:
            messages: 待脱敏的消息列表。
            use_personal: 是否启用了个人知识库。

        返回:
            list[dict]: 脱敏处理后的消息列表。
        """
        if self.policy.privacy_level == "minimal":
            return [{"role": msg.get("role", ""), "content_chars": len(str(msg.get("content", "")))} for msg in messages]
        output = []
        for msg in messages:
            if msg.get("role") == "system" and not self.policy.include_system_prompt:
                output.append({"role": "system", "content": "[redacted: system prompt]"})
                continue
            if use_personal and not self.policy.include_personal_content and msg.get("role") == "tool":
                output.append({**msg, "content": _REDACTED_PERSONAL_TOOL_RESULT})
                continue
            output.append(_truncate(dict(msg), self.policy.max_text_chars))
        return output

    def _sanitize_event(self, event: dict[str, Any], *, use_personal: bool) -> dict[str, Any]:
        """根据隐私策略对单个事件进行脱敏处理。

        在 minimal 级别下仅保留事件类型和工具名/内容长度；
        在其他级别下对包含个人来源的工具结果进行脱敏。

        参数:
            event: 待脱敏的事件字典。
            use_personal: 是否启用了个人知识库。

        返回:
            dict[str, Any]: 脱敏处理后的事件字典。
        """
        event_type = event.get("type")
        sanitized = dict(event)
        if self.policy.privacy_level == "minimal":
            if event_type == "tool_call":
                return {"type": "tool_call", "tool": sanitized.get("tool")}
            if event_type == "tool_result":
                return {"type": "tool_result", "tool": sanitized.get("tool")}
            if event_type in {"text", "error"}:
                return {"type": event_type, "content_chars": len(str(sanitized.get("data", "")))}
            return sanitized

        if (
            event_type == "tool_result"
            and not self.policy.include_personal_content
            and (use_personal or _contains_personal_source(sanitized))
        ):
            sanitized["summary"] = _REDACTED_PERSONAL_TOOL_RESULT
            sanitized["content"] = _REDACTED_PERSONAL_TOOL_RESULT
        return _truncate(sanitized, self.policy.max_event_content_chars)

    def _sanitize_text(self, text: str) -> str:
        """根据隐私策略对文本进行脱敏处理。

        在 minimal 级别下返回空字符串，其他级别下进行截断处理。

        参数:
            text: 待脱敏的文本。

        返回:
            str: 脱敏处理后的文本。
        """
        if self.policy.privacy_level == "minimal":
            return ""
        return _truncate(text or "", self.policy.max_text_chars)


class InteractionRecordingSession:
    """交互记录会话，跟踪单次代理交互的完整生命周期。

    在代理运行期间捕获工具调用、文本回复、引用、基因信息等事件，
    并在完成时持久化到磁盘。
    """

    def __init__(
        self,
        *,
        recorder: InteractionRecorder,
        active: bool,
        record: dict[str, Any],
        use_personal: bool,
    ):
        """初始化交互记录会话。

        参数:
            recorder: 父交互记录器实例。
            active: 此会话是否处于活跃捕获状态。
            record: 交互记录的完整数据字典。
            use_personal: 是否启用了个人知识库。
        """
        self.recorder = recorder
        self.active = active
        self.record = record
        self.use_personal = use_personal
        self._answer_parts: list[str] = []
        self._tools_used: list[str] = []

    @property
    def interaction_id(self) -> str:
        """获取当前交互记录的唯一标识符。

        返回:
            str: 交互记录 ID。
        """
        return str(self.record["interaction_id"])

    @property
    def turn_id(self) -> str:
        """获取当前对话轮次的唯一标识符。

        返回:
            str: 对话轮次 ID。
        """
        return str(self.record["turn_id"])

    def capture_event(self, event: dict[str, Any]) -> None:
        """捕获并记录一个代理事件。

        根据事件类型更新交互记录中的工具使用列表、回答文本、引用、基因信息或错误信息。
        非活跃状态下直接返回不做处理。

        参数:
            event: 事件字典，必须包含 type 字段，可包含 tool、data、genes 等字段。
        """
        if not self.active:
            return
        event_type = event.get("type")
        self.record["events"].append(
            {
                "index": len(self.record["events"]),
                "created_at": _utc_now(),
                "payload": self.recorder._sanitize_event(event, use_personal=self.use_personal),
            }
        )
        if event_type == "tool_call":
            tool = str(event.get("tool") or "")
            if tool and tool not in self._tools_used:
                self._tools_used.append(tool)
        elif event_type == "text":
            self._answer_parts.append(str(event.get("data") or ""))
        elif event_type in {"citations", "sources"}:
            self.record["final"]["citations"] = _truncate(
                event.get("data") or [],
                self.recorder.policy.max_event_content_chars,
            )
        elif event_type == "genes_available":
            self.record["final"]["genes"] = event.get("genes") or []
        elif event_type == "error":
            self.record["final"]["error"] = str(event.get("data") or event.get("msg") or "")

    def finish(self, status: str = "completed") -> None:
        """完成并持久化交互记录。

        设置完成时间和最终状态，组装回答文本和工具使用列表，
        并通过记录器将完整记录写入磁盘。

        参数:
            status: 交互完成状态，默认为 "completed"。
        """
        self.record["completed_at"] = _utc_now()
        self.record["status"] = status if self.active else self.record["status"]
        if not self.active:
            return
        self.record["final"]["answer_text"] = self.recorder._sanitize_text("".join(self._answer_parts))
        self.record["final"]["tools_used"] = list(self._tools_used)
        self.recorder._finish(self.record)


__all__ = [
    "InteractionCapturePolicy",
    "InteractionRecorder",
    "InteractionRecordingSession",
    "PrivacyLevel",
    "SCHEMA_VERSION",
]
