from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_SENSITIVE_DETAIL_KEYS = {"raw_exception", "traceback", "secret", "token", "authorization"}


@dataclass(frozen=True)
class ContractResponse:
    """标准化的 API 响应数据类。

    封装 HTTP 状态码和响应体字典，用于统一 API 错误响应的格式。
    使用 frozen=True 使实例创建后不可变。
    """
    status_code: int
    body: dict[str, Any]


def _sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """过滤掉响应详情中的敏感信息字段。

    移除可能包含机密信息的键（如 raw_exception、traceback、secret、token、authorization），
    防止敏感数据通过 API 响应泄露到客户端。

    参数:
        details: 原始详情字典，可能包含敏感字段。若为 None 或空则返回空字典。

    返回:
        dict: 过滤后的安全详情字典，仅保留非敏感字段。
    """
    if not details:
        return {}
    return {
        key: value
        for key, value in details.items()
        if key.lower() not in _SENSITIVE_DETAIL_KEYS
    }


def error_response(
    *,
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> ContractResponse:
    """构建标准化的错误响应对象。

    生成统一格式的错误响应，自动过滤详情中的敏感字段。
    响应体格式为: {"error": {"code": "...", "message": "...", "details": {...}}}

    参数:
        code: 错误代码标识符（如 "NOT_FOUND"、"VALIDATION_ERROR"）。
        message: 面向用户的错误描述信息。
        status_code: HTTP 状态码（如 400、404、500）。
        details: 可选的错误详情字典，其中的敏感字段会被自动过滤。

    返回:
        ContractResponse: 包含状态码和格式化错误体的响应对象。
    """
    return ContractResponse(
        status_code=status_code,
        body={
            "error": {
                "code": code,
                "message": message,
                "details": _sanitize_details(details),
            }
        },
    )
