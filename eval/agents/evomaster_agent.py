"""
EvoMaster Agent - 基于工具使用的进化式 Agent
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any


class EvoMasterAgent:
    """EvoMaster 工具使用 Agent"""

    def __init__(self, playground: str = "fs_mv", config: str = "", timeout: int = 600):
        self.playground = playground
        self.config = config
        self.timeout = timeout
        self.name = f"EvoMaster-{playground}"

    async def answer(self, question: str) -> dict[str, Any]:
        """
        回答问题

        返回: {"ok": True/False, "output": "答案", "error": "错误信息"}
        """
        try:
            # 动态导入 EvoMaster
            evomaster_root = Path(os.getenv("EVOMASTER_ROOT", "/data/haotianwu/Evomaster_fs"))
            if str(evomaster_root) not in sys.path:
                sys.path.insert(0, str(evomaster_root))

            from evomaster_nutribench_adapter import call_evomaster

            result = await asyncio.wait_for(
                call_evomaster(
                    question,
                    agent_name=self.playground,
                    config_path=self.config or None,
                ),
                timeout=self.timeout,
            )

            answer = result.get("answer", "")
            if not answer.strip():
                return {"ok": False, "error": "EvoMaster 返回空内容", "output": ""}

            return {"ok": True, "output": answer, "error": None}

        except asyncio.TimeoutError:
            return {"ok": False, "error": f"EvoMaster 超时: {self.timeout}秒", "output": ""}
        except Exception as e:
            return {"ok": False, "error": f"EvoMaster 调用失败: {e}", "output": ""}
