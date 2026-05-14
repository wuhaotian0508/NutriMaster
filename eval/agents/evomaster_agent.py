"""
EvoMaster Agent - 基于工具使用的进化式 Agent
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from eval.configs import EVOMASTER_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL


class EvoMasterAgent:
    """EvoMaster 工具使用 Agent"""

    def __init__(
        self,
        playground: str = "fs_mv",
        config: str = "",
        timeout: int = 3600,
        model: str = EVOMASTER_MODEL,
        api_key: str | None = OPENAI_API_KEY,
        base_url: str | None = OPENAI_BASE_URL,
    ):
        self.playground = playground
        self.config = config
        self.timeout = timeout
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.name = f"EvoMaster-{playground}"

    def _prepare_env(self) -> None:
        """Prepare OpenAI-compatible env vars consumed by EvoMaster config.yaml."""
        if self.model:
            os.environ["MAIN_MODEL"] = self.model
        if self.api_key:
            os.environ["OPENAI_API_KEY"] = self.api_key
        if self.base_url:
            os.environ["OPENAI_BASE_URL"] = self.base_url

    async def answer(self, question: str) -> dict[str, Any]:
        """
        回答问题

        返回: {"ok": True/False, "output": "答案", "error": "错误信息"}
        """
        try:
            self._prepare_env()

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
                    timeout=self.timeout,
                ),
                timeout=self.timeout,
            )

            if not result.get("ok", True):
                return {
                    "ok": False,
                    "error": f"EvoMaster 调用失败: {result.get('error') or '未知错误'}",
                    "output": "",
                }

            answer = result.get("answer") or result.get("output") or ""
            if not answer.strip():
                return {"ok": False, "error": "EvoMaster 返回空内容", "output": ""}

            return {"ok": True, "output": answer, "error": None}

        except asyncio.TimeoutError:
            return {"ok": False, "error": f"EvoMaster 超时: {self.timeout}秒", "output": ""}
        except Exception as e:
            return {"ok": False, "error": f"EvoMaster 调用失败: {e}", "output": ""}
