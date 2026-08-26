import os

import pytest
import requests
from dotenv import load_dotenv
from openai import OpenAI

from nutrimaster.config.settings import Settings
from nutrimaster.rag.jina import _build_headers, _post_with_retry


load_dotenv()

REQUIRED_REAL_SERVICE_ENV = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "MAIN_MODEL",
    "JINA_API_KEY",
]


def _require_env(keys: list[str]) -> dict[str, str]:
    missing = [key for key in keys if not os.getenv(key)]
    assert not missing, f"Missing required real-service env vars: {', '.join(missing)}"
    return {key: os.environ[key] for key in keys}


@pytest.mark.integration
def test_real_llm_chat_completion_returns_text():
    env = _require_env(["OPENAI_API_KEY", "OPENAI_BASE_URL", "MAIN_MODEL"])
    assert env["MAIN_MODEL"] == "deepseek-v4-flash", (
        "release integration must exercise deepseek-v4-flash; model fallback is forbidden"
    )
    client = OpenAI(
        api_key=env["OPENAI_API_KEY"],
        base_url=env["OPENAI_BASE_URL"],
        max_retries=4,
        timeout=60,
    )

    response = client.chat.completions.create(
        model=env["MAIN_MODEL"],
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly: nutrimaster-ok",
            }
        ],
        temperature=0,
        # deepseek-v4-flash may spend an initial bounded portion of the
        # completion on reasoning_content. Sixteen tokens can produce HTTP
        # 200 with no final content and is therefore not a valid availability
        # probe for the exact production model.
        max_tokens=64,
    )

    content = response.choices[0].message.content or ""
    assert response.model == "deepseek-v4-flash"
    assert "nutrimaster-ok" in content.lower()


@pytest.mark.integration
def test_real_jina_embedding_returns_vector():
    env = _require_env(["JINA_API_KEY"])
    settings = Settings.from_env()
    assert settings.rag is not None

    # Exercise the same on-demand Clash lifecycle and explicit per-request
    # proxy kwargs used by the application. A raw requests.post() here would
    # test a different network path and can falsely report the deployed Jina
    # integration as unavailable.
    payload = _post_with_retry(
        settings.rag.jina_embedding_url,
        {
            "model": "jina-embeddings-v3",
            "input": ["NutriMaster validates plant nutrition gene retrieval."],
            "task": "retrieval.passage",
        },
        _build_headers(env["JINA_API_KEY"]),
        timeout=60,
        max_retries=3,
    )

    vector = payload["data"][0]["embedding"]
    assert isinstance(vector, list)
    assert len(vector) > 100


@pytest.mark.integration
def test_real_pubmed_search_is_reachable():
    try:
        response = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": "tomato lycopene biosynthesis",
                "retmode": "json",
                "retmax": "1",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        pytest.fail(f"PubMed real-service request failed: {exc}")

    assert response.status_code == 200, response.text[:500]
    payload = response.json()
    assert payload["esearchresult"]["count"].isdigit()
