from __future__ import annotations

import logging

from nutrimaster.agent.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册表，管理代理可用工具的注册、查找和执行。"""

    def __init__(self):
        """初始化工具注册表，创建空的工具存储字典。"""
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具到注册表中。

        如果同名工具已存在，会记录警告日志并替换为新实例。

        参数:
            tool: 要注册的工具实例，必须是 BaseTool 的子类。

        异常:
            TypeError: 当 tool 不是 BaseTool 的子类时抛出。
        """
        if not isinstance(tool, BaseTool):
            raise TypeError(
                f"Tool {tool!r} must inherit BaseTool; got {type(tool).__name__}"
            )
        if tool.name in self._tools:
            logger.warning("Tool %r registered more than once; replacing previous instance", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """按名称查找已注册的工具。

        参数:
            name: 工具名称。

        返回:
            BaseTool | None: 找到的工具实例，不存在时返回 None。
        """
        return self._tools.get(name)

    def list_all(self) -> list[dict]:
        """列出所有已注册工具的名称和描述。

        返回:
            list[dict]: 包含 name 和 description 的字典列表。
        """
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
        ]

    @property
    def tool_names(self) -> set[str]:
        """获取所有已注册工具的名称集合。

        返回:
            set[str]: 工具名称集合。
        """
        return set(self._tools)

    @property
    def get_definitions(self) -> list[dict]:
        """获取所有已注册工具的 OpenAI 兼容 schema 定义列表。

        返回:
            list[dict]: 工具 schema 定义列表，用于 LLM 的 function calling。
        """
        return [tool.schema for tool in self._tools.values()]

    async def execute(self, name: str, **params):
        """执行指定名称的工具。

        参数:
            name: 工具名称。
            **params: 传递给工具 execute 方法的参数。

        返回:
            工具执行的返回结果。

        异常:
            ValueError: 当指定名称的工具未注册时抛出。
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        return await tool.execute(**params)

    def __repr__(self) -> str:
        """返回工具注册表的字符串表示。

        返回:
            str: 包含所有已注册工具名称的可读字符串。
        """
        return f"ToolRegistry(tools={list(self._tools)})"
