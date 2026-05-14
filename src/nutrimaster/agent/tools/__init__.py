# 工具模块导出
# 各工具的具体实现位置：
#
# 1. BaseTool (抽象基类): src/nutrimaster/agent/tools/base.py (第11-26行)
#    class BaseTool(ABC):
#        name: ClassVar[str]
#        description: ClassVar[str]
#        @property
#        @abstractmethod
#        def schema(self) -> dict: ...
#        @abstractmethod
#        async def execute(self, **kwargs) -> Any: ...
#
# 2. ToolError (异常类): src/nutrimaster/agent/tools/base.py (第7-9行)
#    class ToolError(Exception):
#        """Raised by tools for expected tool execution failures."""
#
# 3. ExperimentDesignTool: src/nutrimaster/agent/tools/experiment.py (第21-71行)
#    依赖服务: src/nutrimaster/experiment/service.py → ExperimentDesignService
#    核心方法: ExperimentDesignService.tool_call() (第47-63行)
#
# 4. RagSearchTool: src/nutrimaster/agent/tools/rag.py (第50-84行)
#    依赖服务: src/nutrimaster/rag/service.py → RAGSearchService
#    核心方法: RAGSearchService.search() (第48-79行)
#    数据结构: src/nutrimaster/rag/evidence.py → EvidencePacket (第134-167行)
#
# 5. ToolRegistry (工具注册器): src/nutrimaster/agent/tools/registry.py (第10-48行)
#    主要方法:
#      - register(tool: BaseTool) -> None  (第14-21行)
#      - get(name: str) -> BaseTool | None  (第23-24行)
#      - execute(name: str, **params)  (第40-44行)

from nutrimaster.agent.tools.base import BaseTool, ToolError
from nutrimaster.agent.tools.experiment import ExperimentDesignTool
from nutrimaster.agent.tools.rag import RagSearchTool
from nutrimaster.agent.tools.registry import ToolRegistry

Toolregistry = ToolRegistry

__all__ = [
    "BaseTool",
    "ExperimentDesignTool",
    "RagSearchTool",
    "ToolError",
    "ToolRegistry",
    "Toolregistry",
]
