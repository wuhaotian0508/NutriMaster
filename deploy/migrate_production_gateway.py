#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_MODEL = "deepseek-v4-flash"
GATEWAY_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "MAIN_MODEL")
MAX_ENV_BYTES = 1024 * 1024
_ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?)"
    r"(?P<key>OPENAI_API_KEY|OPENAI_BASE_URL|MAIN_MODEL)"
    r"(?P<separator>\s*=\s*)(?P<value>.*)$"
)


def _assert_regular_file(path: Path) -> os.stat_result:
    details = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(details.st_mode):
        raise RuntimeError(f"environment path is not a regular file: {path}")
    if details.st_size > MAX_ENV_BYTES:
        raise RuntimeError(f"environment file exceeds {MAX_ENV_BYTES} bytes: {path}")
    return details


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def read_gateway(path: Path) -> tuple[dict[str, str], bytes]:
    _assert_regular_file(path)
    raw = path.read_bytes()
    if len(raw) > MAX_ENV_BYTES or b"\0" in raw:
        raise RuntimeError(f"environment file is invalid: {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"environment file is not UTF-8: {path}") from exc

    values: dict[str, str] = {}
    counts = {key: 0 for key in GATEWAY_KEYS}
    for line in text.splitlines():
        match = _ASSIGNMENT_RE.fullmatch(line)
        if not match:
            continue
        key = match.group("key")
        counts[key] += 1
        values[key] = _unquote(match.group("value"))
    if any(counts[key] != 1 for key in GATEWAY_KEYS):
        raise RuntimeError(f"gateway keys must each occur exactly once in {path}")
    if any(not values[key] for key in GATEWAY_KEYS):
        raise RuntimeError(f"gateway values must not be empty in {path}")
    if values["MAIN_MODEL"] != EXPECTED_MODEL:
        raise RuntimeError(f"MAIN_MODEL must be exactly {EXPECTED_MODEL} in {path}")
    base = urlsplit(values["OPENAI_BASE_URL"])
    if base.scheme not in {"http", "https"} or not base.netloc or base.query or base.fragment:
        raise RuntimeError(f"OPENAI_BASE_URL is invalid in {path}")
    for value in values.values():
        if any(character in value for character in ("\0", "\r", "\n")):
            raise RuntimeError(f"gateway value contains a control character in {path}")
    return values, raw


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def render_gateway(destination_raw: bytes, gateway: dict[str, str]) -> bytes:
    text = destination_raw.decode("utf-8")
    replaced = {key: 0 for key in GATEWAY_KEYS}
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        match = _ASSIGNMENT_RE.fullmatch(content)
        if not match:
            output.append(line)
            continue
        key = match.group("key")
        replaced[key] += 1
        output.append(
            f"{match.group('prefix')}{key}{match.group('separator')}"
            f"{_shell_quote(gateway[key])}{ending}"
        )
    if any(replaced[key] != 1 for key in GATEWAY_KEYS):
        raise RuntimeError("destination gateway keys changed during migration")
    return "".join(output).encode("utf-8")


def probe_gateway(gateway: dict[str, str], *, timeout: float = 90.0) -> dict[str, object]:
    payload = json.dumps(
        {
            "model": EXPECTED_MODEL,
            "messages": [
                {"role": "user", "content": "Reply with exactly: nutrimaster-ok"}
            ],
            "temperature": 0,
            # deepseek-v4-flash can emit reasoning_content before content. A
            # 16-token probe can therefore return HTTP 200 with no final text.
            "max_tokens": 64,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        gateway["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions",
        data=payload,
        headers={
            "Authorization": "Bearer " + gateway["OPENAI_API_KEY"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        message = ((body.get("choices") or [{}])[0].get("message") or {})
        content = message.get("content") or ""
        ok = (
            response.status == 200
            and body.get("model") == EXPECTED_MODEL
            and "nutrimaster-ok" in content.lower()
        )
        return {
            "ok": ok,
            "http_status": response.status,
            "response_model_ok": body.get("model") == EXPECTED_MODEL,
            "content_ok": "nutrimaster-ok" in content.lower(),
        }
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            body = {}
        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            error = {}
        return {
            "ok": False,
            "http_status": exc.code,
            "error_code": error.get("code"),
            "error_type": error.get("type"),
        }
    except Exception as exc:
        return {"ok": False, "exception_type": type(exc).__name__}


def _exclusive_backup(path: Path, data: bytes, *, uid: int, gid: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchown(descriptor, uid, gid)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, data: bytes, *, uid: int, gid: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.gateway-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, uid, gid)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def migrate_gateway(source: Path, destination: Path, backup: Path) -> dict[str, object]:
    _assert_regular_file(source)
    source = source.resolve(strict=True)
    destination_details = _assert_regular_file(destination)
    source_gateway, _ = read_gateway(source)
    destination_gateway, destination_raw = read_gateway(destination)

    gateways_match = all(
        destination_gateway[key] == source_gateway[key] for key in GATEWAY_KEYS
    )
    if gateways_match:
        destination_probe = probe_gateway(destination_gateway)
        if not destination_probe.get("ok"):
            raise RuntimeError("the matching live gateway failed the exact-model probe")
        os.chmod(destination, 0o600)
        return {"status": "ok", "action": "unchanged", "probe": destination_probe}

    source_probe = probe_gateway(source_gateway)
    if not source_probe.get("ok"):
        raise RuntimeError("the live legacy gateway failed the exact-model probe")
    if backup.exists() or backup.is_symlink():
        raise RuntimeError(f"refusing to overwrite gateway backup: {backup}")
    if not backup.parent.is_dir() or backup.parent.is_symlink():
        raise RuntimeError(f"gateway backup directory is missing or unsafe: {backup.parent}")

    replacement = render_gateway(destination_raw, source_gateway)
    _exclusive_backup(
        backup,
        destination_raw,
        uid=destination_details.st_uid,
        gid=destination_details.st_gid,
    )
    _atomic_write(
        destination,
        replacement,
        uid=destination_details.st_uid,
        gid=destination_details.st_gid,
    )
    migrated_gateway, migrated_raw = read_gateway(destination)
    migrated_probe = probe_gateway(migrated_gateway)
    if not migrated_probe.get("ok"):
        _atomic_write(
            destination,
            destination_raw,
            uid=destination_details.st_uid,
            gid=destination_details.st_gid,
        )
        raise RuntimeError("migrated gateway probe failed; destination was restored")
    if hashlib.sha256(migrated_raw).digest() != hashlib.sha256(replacement).digest():
        raise RuntimeError("migrated environment changed after atomic publication")
    return {"status": "ok", "action": "migrated", "probe": migrated_probe}


def check_gateway(source: Path, destination: Path) -> dict[str, object]:
    _assert_regular_file(source)
    _assert_regular_file(destination)
    source_gateway, _ = read_gateway(source)
    destination_gateway, _ = read_gateway(destination)
    if any(destination_gateway[key] != source_gateway[key] for key in GATEWAY_KEYS):
        raise RuntimeError("candidate gateway does not match the proven live gateway")
    probe = probe_gateway(destination_gateway)
    if not probe.get("ok"):
        raise RuntimeError("candidate gateway failed the exact-model probe")
    return {"status": "ok", "action": "checked", "probe": probe}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Atomically hand off the proven live DeepSeek gateway"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.check:
        if args.backup is not None:
            raise RuntimeError("--backup is forbidden with --check")
        result = check_gateway(args.source, args.destination)
    else:
        if args.backup is None:
            raise RuntimeError("--backup is required unless --check is used")
        result = migrate_gateway(args.source, args.destination, args.backup)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
