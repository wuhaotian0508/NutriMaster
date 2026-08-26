#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests


EXPECTED_MODEL = "deepseek-v4-flash"
_SEARCHES = (
    {
        "query": "Arabidopsis nitrogen uptake NRT1.1 regulation",
        "pubmed_query": "Arabidopsis NRT1.1 nitrogen uptake",
        "gene_db_query": "Arabidopsis thaliana NRT1.1",
        "focus": "mechanism",
    },
    {
        "query": "rice phosphate starvation PHR2 pathway",
        "pubmed_query": "rice PHR2 phosphate starvation",
        "gene_db_query": "Oryza sativa PHR2",
        "focus": "pathway",
    },
    {
        "query": "maize iron homeostasis transporter genes",
        "pubmed_query": "maize iron homeostasis transporter",
        "gene_db_query": "Zea mays iron transporter",
        "focus": "gene",
    },
    {
        "query": "plant potassium channel AKT1 regulation",
        "pubmed_query": "plant AKT1 potassium channel regulation",
        "gene_db_query": "Arabidopsis AKT1",
        "focus": "general",
    },
)


def _events(response: requests.Response) -> list[dict[str, Any]]:
    response.raise_for_status()
    events: list[dict[str, Any]] = []
    for raw in response.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        payload = raw[5:].strip()
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _assert_sse_success(label: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        raise RuntimeError(f"{label} returned no SSE events")
    errors = [event for event in events if event.get("type") == "error"]
    if errors:
        raise RuntimeError(f"{label} returned an error event: {errors[0].get('data')}")
    event_types = {str(event.get("type")) for event in events}
    if not event_types & {"text", "answer", "content", "tool_call", "tool_result"}:
        raise RuntimeError(f"{label} returned no answer/tool event: {sorted(event_types)}")
    return {"event_count": len(events), "event_types": sorted(event_types)}


def run_smoke(
    *,
    base_url: str,
    token: str,
    expected_generation: str,
    public_url: str | None,
) -> dict[str, Any]:
    if not token or len(token) > 16_384:
        raise RuntimeError("a bounded authenticated bearer token is required")
    base_url = base_url.rstrip("/")
    session = requests.Session()
    session.trust_env = False
    headers = {"Authorization": f"Bearer {token}"}

    health = session.get(f"{base_url}/api/health", timeout=10)
    health.raise_for_status()
    health_payload = health.json()
    generation = (health_payload.get("index") or {}).get("generation_id")
    if health_payload.get("status") != "ok" or generation != expected_generation:
        raise RuntimeError(
            f"health generation mismatch: status={health_payload.get('status')!r}, "
            f"generation={generation!r}, expected={expected_generation!r}"
        )

    unauthenticated = session.post(
        f"{base_url}/api/rag/search",
        json={"query": "authorization boundary"},
        timeout=10,
    )
    if unauthenticated.status_code != 401:
        raise RuntimeError(
            f"unauthenticated RAG search must return 401, got {unauthenticated.status_code}"
        )

    def search(payload: dict[str, str]) -> dict[str, Any]:
        response = session.post(
            f"{base_url}/api/rag/search",
            headers=headers,
            json={**payload, "mode": "deep", "top_k": 3},
            timeout=180,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("text") or not isinstance(result.get("citations"), list):
            raise RuntimeError(f"RAG result is incomplete for query: {payload['query']}")
        counts = result.get("source_counts")
        if not isinstance(counts, dict) or sum(
            value for value in counts.values() if isinstance(value, int)
        ) <= 0:
            raise RuntimeError(f"RAG result has no retrieval hits: {payload['query']}")
        return {
            "query": payload["query"],
            "source_counts": counts,
            "citations": len(result["citations"]),
            "graph_evidence": len(result.get("graph_evidence") or []),
        }

    with ThreadPoolExecutor(max_workers=4) as executor:
        searches = list(executor.map(search, _SEARCHES))

    legacy = session.post(
        f"{base_url}/api/query",
        headers=headers,
        json={
            "query": "Briefly explain how NRT1.1 affects plant nitrogen uptake.",
            "history": [],
            "use_personal": False,
            "use_depth": False,
            "model_id": EXPECTED_MODEL,
            "capture_consent": False,
        },
        stream=True,
        timeout=360,
    )
    legacy_summary = _assert_sse_success("legacy /api/query", _events(legacy))

    pi = session.post(
        f"{base_url}/api/pi/query",
        headers=headers,
        json={
            "query": "Use the retrieval tool and briefly explain NRT1.1 nitrogen uptake.",
            "history": [],
            "use_personal": False,
            "use_depth": False,
            "capture_consent": False,
        },
        stream=True,
        timeout=360,
    )
    pi_summary = _assert_sse_success("Pi /api/pi/query", _events(pi))

    public_boundary = None
    if public_url:
        public = requests.Session()
        public.trust_env = False
        statuses = {}
        for path in ("/api/pi/internal", "/api/pi/internal/probe"):
            response = public.get(f"{public_url.rstrip('/')}{path}", timeout=15)
            statuses[path] = response.status_code
        if set(statuses.values()) != {404}:
            raise RuntimeError(f"public Pi callback boundary is open: {statuses}")
        public_boundary = statuses

    return {
        "status": "ok",
        "expected_model": EXPECTED_MODEL,
        "generation_id": generation,
        "four_searches": searches,
        "legacy_sse": legacy_summary,
        "pi_sse": pi_summary,
        "public_internal_boundary": public_boundary,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authenticated NutriMaster production smoke")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--expected-generation", required=True)
    parser.add_argument("--public-url")
    parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="read the bearer token from one stdin line so it never appears in argv",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.token_stdin:
        raise RuntimeError("--token-stdin is required")
    token = sys.stdin.readline().strip()
    result = run_smoke(
        base_url=args.base_url,
        token=token,
        expected_generation=args.expected_generation,
        public_url=args.public_url,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
