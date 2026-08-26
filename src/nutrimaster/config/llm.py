from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, OpenAI, Timeout

from nutrimaster.config.settings import Settings

logger = logging.getLogger(__name__)

_DEEPSEEK_REASONER_MODELS = {"deepseek-reasoner", "deepseek-r1", "deepseek-v4"}
_DEEPSEEK_IGNORED_PARAMS = {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
_DEEPSEEK_FORBIDDEN_PARAMS = {"logprobs", "top_logprobs"}
_DEEPSEEK_DEFAULT_THINKING = {"type": "enabled"}
_DEEPSEEK_DEFAULT_EFFORT = "high"
_TIMEOUT = Timeout(timeout=300.0, connect=10.0)

_GLOBAL_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def clear_global_proxy_env() -> None:
    """Prevent non-Jina outbound clients from inheriting shell-level Clash proxy."""
    for key in _GLOBAL_PROXY_ENV_KEYS:
        os.environ.pop(key, None)



def is_deepseek_reasoner(model_name: str) -> bool:
    """判断给定的模型名称是否为 DeepSeek 推理模型。

    通过将模型名称转为小写后，检查是否包含已知的 DeepSeek 推理模型标识符。

    Args:
        model_name: 模型名称字符串。

    Returns:
        如果模型属于 DeepSeek 推理模型则返回 True，否则返回 False。
    """
    name = model_name.lower()
    return any(model in name for model in _DEEPSEEK_REASONER_MODELS)


def sanitize_params_for_model(
    model_name: str,
    params: dict[str, Any],
    *,
    is_agent_call: bool = False,
) -> dict[str, Any]:
    """根据模型类型清洗和调整 LLM 调用参数。

    对于 DeepSeek 推理模型，会移除不支持的参数（如 temperature、top_p 等），
    并自动添加思维链（thinking）和推理力度（reasoning_effort）的默认配置。
    对于非 DeepSeek 推理模型，参数原样返回。

    Args:
        model_name: 模型名称，用于判断是否需要特殊处理。
        params: 原始的 LLM 调用参数字典。
        is_agent_call: 是否为 Agent 调用。如果为 True，推理力度设为 "max"，
            否则使用默认值 "high"。

    Returns:
        清洗后的参数字典（不修改原始字典，返回新副本）。
    """
    params = dict(params)
    if not is_deepseek_reasoner(model_name):
        return params
    for key in _DEEPSEEK_IGNORED_PARAMS | _DEEPSEEK_FORBIDDEN_PARAMS:
        params.pop(key, None)
    extra_body = dict(params.get("extra_body", {}))
    extra_body.setdefault("thinking", _DEEPSEEK_DEFAULT_THINKING.copy())
    extra_body.setdefault("reasoning_effort", "max" if is_agent_call else _DEEPSEEK_DEFAULT_EFFORT)
    params["extra_body"] = extra_body
    return params


@dataclass(frozen=True)
class LLMRoute:
    """LLM 路由配置，将客户端实例与模型名称绑定。

    Attributes:
        client: OpenAI 兼容的 API 客户端实例（同步或异步）。
        model: 该路由使用的模型名称。
    """
    client: Any
    model: str


class LLMGateway:
    """LLM 网关，支持多路由的统一调用接口。

    通过路由表管理多个 LLM 客户端，提供统一的普通调用和流式调用方法。
    默认使用 "primary" 路由。
    """

    def __init__(self, *, routes: dict[str, LLMRoute]):
        """初始化 LLM 网关。

        Args:
            routes: 路由字典，键为路由标识符（如 "primary"），值为 LLMRoute 实例。
                必须包含 "primary" 路由作为默认回退。
        """
        self.routes = routes

    def _resolve(self, model_id: str = "") -> tuple[str, LLMRoute]:
        """根据模型标识符解析对应的路由。

        Args:
            model_id: 路由标识符。为空字符串时使用 "primary" 路由。

        Returns:
            元组 (路由标识符, LLMRoute 实例)。如果指定的路由不存在，回退到 "primary"。
        """
        route_id = model_id or "primary"
        return route_id, self.routes.get(route_id) or self.routes["primary"]

    async def call_llm(
        self,
        messages,
        tools=None,
        model_id: str = "",
        is_agent_call: bool = False,
        **kwargs,
    ):
        """异步调用 LLM 并返回响应消息。

        根据指定的路由发送聊天补全请求，自动针对模型类型清洗参数。

        Args:
            messages: 聊天消息列表，符合 OpenAI 消息格式。
            tools: 可选的工具定义列表，用于函数调用（function calling）。
            model_id: 路由标识符，为空则使用默认 "primary" 路由。
            is_agent_call: 是否为 Agent 调用，影响 DeepSeek 模型的推理力度设置。
            **kwargs: 其他传递给 chat.completions.create 的参数。

        Returns:
            LLM 响应中第一个选项的消息对象。

        Raises:
            Exception: LLM 调用失败时抛出原始异常。
        """
        route_id, route = self._resolve(model_id)
        try:
            params = {"model": route.model, "messages": messages, **kwargs}
            if tools:
                params["tools"] = tools
            params = sanitize_params_for_model(
                route.model,
                params,
                is_agent_call=is_agent_call,
            )
            response = await route.client.chat.completions.create(**params)
            return response.choices[0].message
        except Exception as exc:
            logger.warning("[%s] LLM call failed: %s", route_id, exc)
            raise

    async def call_llm_stream(
        self,
        messages,
        model_id: str = "",
        is_agent_call: bool = False,
        **kwargs,
    ):
        """异步流式调用 LLM，逐块返回响应。

        以流式方式发送聊天补全请求，适用于需要实时展示生成内容的场景。

        Args:
            messages: 聊天消息列表，符合 OpenAI 消息格式。
            model_id: 路由标识符，为空则使用默认 "primary" 路由。
            is_agent_call: 是否为 Agent 调用，影响 DeepSeek 模型的推理力度设置。
            **kwargs: 其他传递给 chat.completions.create 的参数。

        Yields:
            流式响应的每个数据块（chunk）。

        Raises:
            Exception: LLM 流式调用失败时抛出原始异常。
        """
        route_id, route = self._resolve(model_id)
        try:
            params = {"model": route.model, "messages": messages, "stream": True, **kwargs}
            params = sanitize_params_for_model(
                route.model,
                params,
                is_agent_call=is_agent_call,
            )
            response = await route.client.chat.completions.create(**params)
            async for chunk in response:
                yield chunk
        except Exception as exc:
            logger.warning("[%s] LLM stream failed: %s", route_id, exc)
            raise


def create_llm_gateway(settings: Settings | None = None) -> LLMGateway:
    """创建并返回一个 LLM 网关实例。

    使用提供的配置（或从环境变量加载默认配置）初始化异步 OpenAI 客户端，
    并将其注册为 "primary" 路由。

    Args:
        settings: 可选的 Settings 实例。为 None 时自动从环境变量加载。

    Returns:
        配置好的 LLMGateway 实例。
    """
    settings = settings or Settings.from_env()
    clear_global_proxy_env()
    routes = {
        "primary": LLMRoute(
            client=AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                timeout=_TIMEOUT,
            ),
            model=settings.model,
        )
    }
    return LLMGateway(routes=routes)


def create_sync_client(settings: Settings | None = None):
    """创建并返回一个同步 OpenAI 客户端实例。

    Args:
        settings: 可选的 Settings 实例。为 None 时自动从环境变量加载。

    Returns:
        配置好的同步 OpenAI 客户端实例。
    """
    settings = settings or Settings.from_env()
    clear_global_proxy_env()
    return OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=_TIMEOUT,
    )


_DEFAULT_GATEWAY: LLMGateway | None = None
_DEFAULT_SYNC_CLIENT = None


def default_gateway() -> LLMGateway:
    """获取全局默认的 LLM 网关单例。

    首次调用时创建网关实例并缓存，后续调用直接返回缓存实例。

    Returns:
        全局默认的 LLMGateway 实例。
    """
    global _DEFAULT_GATEWAY
    if _DEFAULT_GATEWAY is None:
        _DEFAULT_GATEWAY = create_llm_gateway()
    return _DEFAULT_GATEWAY


async def call_llm(*args, **kwargs):
    """异步调用 LLM 的便捷函数。

    使用全局默认网关发送请求，参数直接透传给 LLMGateway.call_llm。

    Returns:
        LLM 响应中第一个选项的消息对象。
    """
    return await default_gateway().call_llm(*args, **kwargs)


async def call_llm_stream(*args, **kwargs):
    """异步流式调用 LLM 的便捷函数。

    使用全局默认网关发送流式请求，参数直接透传给 LLMGateway.call_llm_stream。

    Yields:
        流式响应的每个数据块（chunk）。
    """
    async for chunk in default_gateway().call_llm_stream(*args, **kwargs):
        yield chunk


def call_llm_sync(
    messages,
    is_agent_call: bool = False,
    *,
    model: str | None = None,
    **kwargs,
):
    """同步调用 LLM 的便捷函数。

    使用全局默认同步客户端发送请求。首次调用时自动创建客户端并缓存。

    Args:
        messages: 聊天消息列表，符合 OpenAI 消息格式。
        is_agent_call: 是否为 Agent 调用，影响 DeepSeek 模型的推理力度设置。
        model: 可选的模型覆盖；未传入时使用 MAIN_MODEL。
        **kwargs: 其他传递给 chat.completions.create 的参数。

    Returns:
        LLM 响应中第一个选项的消息对象。
    """
    global _DEFAULT_SYNC_CLIENT
    settings = Settings.from_env()
    if _DEFAULT_SYNC_CLIENT is None:
        _DEFAULT_SYNC_CLIENT = create_sync_client(settings)
    selected_model = model or settings.model
    params = {"model": selected_model, "messages": messages, **kwargs}
    params = sanitize_params_for_model(
        selected_model,
        params,
        is_agent_call=is_agent_call,
    )
    response = _DEFAULT_SYNC_CLIENT.chat.completions.create(**params)
    return response.choices[0].message
