import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.agents.evomaster_agent import EvoMasterAgent


def test_evomaster_agent_accepts_adapter_output_field(monkeypatch):
    async def call_evomaster(question, agent_name, config_path, timeout):
        assert timeout == 1
        return {"ok": True, "output": f"answer: {question}"}

    module = types.ModuleType("evomaster_nutribench_adapter")
    module.call_evomaster = call_evomaster
    monkeypatch.setitem(sys.modules, "evomaster_nutribench_adapter", module)

    agent = EvoMasterAgent(timeout=1)
    result = asyncio.run(agent.answer("question"))

    assert result == {"ok": True, "output": "answer: question", "error": None}


def test_evomaster_agent_preserves_adapter_error(monkeypatch):
    async def call_evomaster(question, agent_name, config_path, timeout):
        assert timeout == 1
        return {"ok": False, "error": "candidate_2 did not create required solution file", "output": ""}

    module = types.ModuleType("evomaster_nutribench_adapter")
    module.call_evomaster = call_evomaster
    monkeypatch.setitem(sys.modules, "evomaster_nutribench_adapter", module)

    agent = EvoMasterAgent(timeout=1)
    result = asyncio.run(agent.answer("question"))

    assert result["ok"] is False
    assert result["output"] == ""
    assert "candidate_2 did not create required solution file" in result["error"]
