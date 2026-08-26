from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from nutrimaster.config.settings import Settings
from nutrimaster.rag.index_build_jobs import (
    IndexBuildQueue,
    IndexBuildWorker,
    recover_interrupted_build,
)
from nutrimaster.rag.index_generation import resolve_active_generation, validate_generation_manifest
from nutrimaster.rag.legacy_bootstrap import (
    bootstrap_legacy_generation,
    preflight_legacy_generation,
    recover_legacy_bootstrap,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nutrimaster-index-builder",
        description="Run the isolated immutable-generation index builder",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-once")
    subparsers.add_parser("recover-interrupted")
    subparsers.add_parser("bootstrap-legacy")
    subparsers.add_parser("preflight-legacy")
    subparsers.add_parser("recover-bootstrap")
    subparsers.add_parser("verify-active")
    subparsers.add_parser("status")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    queue = IndexBuildQueue.from_settings(settings)
    if args.command == "run-once":
        result = IndexBuildWorker(settings, queue=queue).run_once()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if result.get("state") == "failed" else 0
    if args.command == "recover-interrupted":
        result = recover_interrupted_build(settings, queue=queue)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        # ExecStopPost must finish even when it reports the original service
        # failure; rollback details remain durable in status.json.
        return 0
    if args.command in {"bootstrap-legacy", "preflight-legacy"}:
        source = os.getenv("NUTRIMASTER_LEGACY_INDEX_SOURCE", "")
        if not source:
            raise RuntimeError(
                f"NUTRIMASTER_LEGACY_INDEX_SOURCE is required for {args.command}"
            )
        result = (
            bootstrap_legacy_generation(settings, source_dir=source)
            if args.command == "bootstrap-legacy"
            else preflight_legacy_generation(settings, source_dir=source)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "recover-bootstrap":
        result = recover_legacy_bootstrap(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "verify-active":
        if settings.rag is None:
            raise RuntimeError("RAG settings failed to initialize")
        resolved = resolve_active_generation(
            settings.rag.index_dir,
            require_generation=True,
            validate_artifact_contracts=True,
        )
        manifest = validate_generation_manifest(resolved.path, verify_checksums=False)
        require_graph = os.getenv("NUTRIMASTER_REQUIRE_GRAPH_INDEX", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if require_graph and "graph" not in manifest["artifacts"]:
            raise RuntimeError("required graph artifact is missing from the active generation")
        print(json.dumps({
            "status": "ok",
            "generation_id": resolved.generation_id,
            "generation_dir": str(resolved.path),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps(queue.status(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
