"""Factories for creating eval agents."""

from collections.abc import Iterable, Iterator

from eval.agents.evomaster_agent import EvoMasterAgent
from eval.agents.llm_agent import LLMAgent
from eval.agents.nutrimaster_agent import NutriMasterAgent
from eval.configs import (
    EVOMASTER_CONFIG,
    EVOMASTER_MODEL,
    EVOMASTER_PLAYGROUND,
    EVOMASTER_TIMEOUT,
    LLM_AGENTS,
    LLM_API_KEY,
    LLM_BASE_URL,
    NUTRIMASTER_MODEL_ID,
    NUTRIMASTER_USE_DEPTH,
)
from eval.contracts import EvalAgent


def _normalize_model_alias(value: str) -> str:
    return value.lower().replace("vendor2/", "").replace("-", "").replace("_", "").replace(".", "")


def select_llm_agents(llm_model: str | None) -> list[dict[str, str]]:
    """Select LLM configs; when omitted, return configured default baselines."""
    if not llm_model:
        return list(LLM_AGENTS)

    selected: list[dict[str, str]] = []
    by_alias: dict[str, dict[str, str]] = {}
    for config in LLM_AGENTS:
        aliases = {
            config["id"],
            config["short"],
            _normalize_model_alias(config["id"]),
            _normalize_model_alias(config["short"]),
        }
        for alias in aliases:
            by_alias[alias.lower()] = config

    for raw_model in llm_model.split(","):
        model = raw_model.strip()
        if not model:
            continue
        config = by_alias.get(model.lower()) or by_alias.get(_normalize_model_alias(model))
        if config:
            selected.append(config)
        else:
            selected.append({"id": model, "short": model.split("/")[-1]})

    if not selected:
        raise ValueError("--llm-model 没有指定有效模型")
    return selected


def create_agent(agent_type: str, agent_config: dict[str, str] | None = None) -> EvalAgent:
    """Create one eval agent from its CLI type."""
    if agent_type == "llm":
        if not agent_config:
            raise ValueError("LLM agent 需要提供 agent_config")
        return LLMAgent(
            model_id=agent_config["id"],
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
        )
    if agent_type == "nutrimaster":
        return NutriMasterAgent(model_id=NUTRIMASTER_MODEL_ID, use_depth=NUTRIMASTER_USE_DEPTH)
    if agent_type == "evomaster":
        return EvoMasterAgent(
            playground=EVOMASTER_PLAYGROUND,
            config=EVOMASTER_CONFIG,
            timeout=EVOMASTER_TIMEOUT,
            model=EVOMASTER_MODEL,
        )
    raise ValueError(f"未知的 agent 类型: {agent_type}")


def iter_agents(agent_types: Iterable[str], llm_model: str | None = None) -> Iterator[EvalAgent]:
    """Expand CLI agent types into concrete agent instances."""
    for raw_agent_type in agent_types:
        agent_type = raw_agent_type.strip().lower()
        if not agent_type:
            continue
        if agent_type == "llm":
            for llm_config in select_llm_agents(llm_model):
                yield create_agent("llm", llm_config)
            continue
        yield create_agent(agent_type)
