from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class ToolError(Exception):
    """工具执行过程中的预期错误异常。

    当工具在执行过程中遇到可预期的失败时抛出此异常。
    """


class BaseTool(ABC):
    """工具抽象基类，定义了所有代理工具必须实现的接口。

    子类必须定义 name 和 description 类变量，并实现 schema 属性和 execute 方法。
    """
    name: ClassVar[str]
    description: ClassVar[str]

    @property
    @abstractmethod
    def schema(self) -> dict:
        """获取 OpenAI 兼容的函数调用 schema 定义。

        返回:
            dict: 包含 type、function（name、description、parameters）的 schema 字典。
        """

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """执行工具并返回结构化或文本内容。

        参数:
            **kwargs: 工具执行所需的参数，由 schema 定义。

        返回:
            Any: 工具执行结果，可以是文本字符串或结构化对象（如 EvidencePacket）。
        """

    def __repr__(self) -> str:
        """返回工具的字符串表示。

        返回:
            str: 包含类名和工具名的可读字符串。
        """
        return f"<{self.__class__.__name__} name={self.name!r}>"
