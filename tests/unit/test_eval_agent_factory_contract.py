import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval import agent_factory


class DummyLLMAgent:
    def __init__(self, model_id, base_url, api_key):
        self.model_id = model_id
        self.base_url = base_url
        self.api_key = api_key
        self.name = model_id.split("/")[-1]

    async def answer(self, question: str):
        return {"ok": True, "output": question, "error": None}


class DummyNutriMasterAgent:
    def __init__(self, model_id="", use_depth=True):
        self.model_id = model_id
        self.use_depth = use_depth
        self.name = "NutriMaster"

    async def answer(self, question: str):
        return {"ok": True, "output": question, "error": None}


class DummyEvoMasterAgent:
    def __init__(self, playground, config, timeout, model):
        self.playground = playground
        self.config = config
        self.timeout = timeout
        self.model = model
        self.name = f"EvoMaster-{playground}"

    async def answer(self, question: str):
        return {"ok": True, "output": question, "error": None}


def test_select_llm_agents_accepts_known_alias_and_custom_model():
    selected = agent_factory.select_llm_agents("GPT-5.4, custom/model")

    assert selected[0]["id"] == "Vendor2/GPT-5.4"
    assert selected[0]["short"] == "GPT-5.4"
    assert selected[1] == {"id": "custom/model", "short": "model"}


def test_iter_agents_expands_llm_and_regular_agents(monkeypatch):
    monkeypatch.setattr(agent_factory, "LLMAgent", DummyLLMAgent)
    monkeypatch.setattr(agent_factory, "NutriMasterAgent", DummyNutriMasterAgent)
    monkeypatch.setattr(agent_factory, "EvoMasterAgent", DummyEvoMasterAgent)

    agents = list(agent_factory.iter_agents(["llm", "nutrimaster", "evomaster"], llm_model="GPT-5.4"))

    assert [agent.name for agent in agents] == ["GPT-5.4", "NutriMaster", "EvoMaster-fs_mv"]
    assert agents[0].model_id == "Vendor2/GPT-5.4"
    assert agents[1].use_depth is True
    assert agents[2].model == agent_factory.EVOMASTER_MODEL


def test_create_agent_rejects_unknown_agent_type():
    with pytest.raises(ValueError, match="未知的 agent 类型"):
        agent_factory.create_agent("unknown")
