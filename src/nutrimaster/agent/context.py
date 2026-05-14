from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequestContext:
    """代理请求的上下文数据类，封装单次用户请求的所有配置参数。

    属性:
        user_input: 用户输入的查询文本。
        user_id: 用户唯一标识符，为 None 表示匿名用户。
        model_id: 指定使用的 LLM 模型标识符。
        history: 对话历史消息列表，每个元素为包含 role 和 content 的字典。
        use_personal: 是否启用个人知识库检索。
        use_depth: 是否启用深度搜索模式（增加召回预算）。
    """
    user_input: str
    user_id: str | None = None
    model_id: str = ""
    history: list[dict] = field(default_factory=list)
    use_personal: bool = False
    use_depth: bool = False
