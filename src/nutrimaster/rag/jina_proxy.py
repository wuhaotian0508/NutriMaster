from __future__ import annotations

import contextlib
import logging
import os
import shlex
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_DEFAULT_PROXY_URL = "http://127.0.0.1:7890"
_STAMP_FILE = Path("/tmp/nutrimaster-jina-proxy.last_used")
_IDLE_STOP_MARKER = Path("/tmp/nutrimaster-jina-proxy-idle-stop.pid")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _find_dotenv(start: Path) -> Path | None:
    try:
        current = start.resolve()
    except OSError:
        current = start
    if current.is_file():
        current = current.parent
    for candidate_dir in (current, *current.parents):
        candidate = candidate_dir / ".env"
        if candidate.exists():
            return candidate
    return None


def _merged_env() -> dict[str, str]:
    values: dict[str, str] = {}
    seen: set[Path] = set()
    for start in (_project_root(), Path.cwd()):
        dotenv_path = _find_dotenv(start)
        if dotenv_path is None or dotenv_path in seen:
            continue
        seen.add(dotenv_path)
        for key, value in dotenv_values(dotenv_path).items():
            if value is not None:
                values[key] = value
    values.update(os.environ)
    return values


def _as_bool(raw: str | None, default: bool = False) -> bool:
    if raw in (None, ""):
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def _as_int(raw: str | None, default: int) -> int:
    if raw in (None, ""):
        return default
    try:
        return int(str(raw))
    except ValueError:
        return default


def _proxy_enabled(env: dict[str, str]) -> bool:
    raw = env.get("NUTRIMASTER_JINA_PROXY_ENABLED", env.get("JINA_PROXY_ENABLED"))
    return _as_bool(raw, default=False)


def _proxy_url(env: dict[str, str]) -> str:
    return env.get("NUTRIMASTER_JINA_PROXY_URL") or env.get("JINA_PROXY_URL") or _DEFAULT_PROXY_URL


def _proxy_host_port(proxy_url: str) -> tuple[str, int]:
    parsed = urlparse(proxy_url)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    return host, port


def _is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run_command(command: list[str], timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - host dependent.
        return 1, str(exc)
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return result.returncode, output


def _start_commands(env: dict[str, str]) -> list[list[str]]:
    custom = env.get("NUTRIMASTER_CLASH_START_CMD") or env.get("CLASH_START_CMD")
    if custom:
        return [shlex.split(custom)]

    script = env.get("NUTRIMASTER_CLASH_START_SCRIPT") or env.get("CLASH_START_SCRIPT")
    script_path = Path(script or "/data/haotianwu/start-clash.sh")
    return [["/bin/bash", str(script_path)]] if script_path.exists() else []


def _ensure_clash_running(env: dict[str, str], host: str, port: int) -> None:
    if _is_port_open(host, port):
        return

    timeout = _as_int(env.get("NUTRIMASTER_CLASH_START_TIMEOUT_SECONDS"), 15)
    last_error = "no start command configured"
    for command in _start_commands(env):
        returncode, output = _run_command(command, timeout=timeout)
        if returncode != 0:
            last_error = f"{' '.join(command)} failed: {output}"
        else:
            last_error = output or f"{' '.join(command)} completed"

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _is_port_open(host, port):
                logger.info("Clash proxy is ready on %s:%s", host, port)
                return
            time.sleep(0.25)

    raise RuntimeError(
        f"Jina proxy is enabled, but Clash is not available on {host}:{port}. "
        f"Last start result: {last_error}"
    )


def _mark_last_used() -> None:
    try:
        _STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STAMP_FILE.touch()
    except OSError as exc:  # pragma: no cover - host permission dependent.
        logger.debug("Could not update Jina proxy stamp file: %s", exc)


def _stop_command(env: dict[str, str]) -> str:
    custom = env.get("NUTRIMASTER_CLASH_STOP_CMD") or env.get("CLASH_STOP_CMD")
    if custom:
        return custom
    script = shlex.quote(env.get("NUTRIMASTER_CLASH_STOP_SCRIPT") or "/data/haotianwu/stop-clash.sh")
    pattern = shlex.quote(env.get("NUTRIMASTER_CLASH_PROCESS_PATTERN") or "/data/haotianwu/clash -f")
    return f"[ -x {script} ] && /bin/bash {script} >/dev/null 2>&1 || true; pkill -f {pattern} 2>/dev/null || true"


def _schedule_idle_stop(env: dict[str, str]) -> None:
    idle_seconds = _as_int(
        env.get("NUTRIMASTER_JINA_PROXY_IDLE_STOP_SECONDS", env.get("JINA_PROXY_IDLE_STOP_SECONDS")),
        300,
    )
    if idle_seconds <= 0:
        return

    stamp = shlex.quote(str(_STAMP_FILE))
    stop_command = _stop_command(env)
    script = (
        f"sleep {idle_seconds}; "
        f"stamp={stamp}; idle={idle_seconds}; now=$(date +%s); last=0; "
        "[ -e \"$stamp\" ] && last=$(stat -c %Y \"$stamp\" 2>/dev/null || echo 0); "
        "if [ \"$last\" -gt 0 ] && [ $((now - last)) -ge \"$idle\" ]; then "
        f"{stop_command}; fi"
    )

    try:
        if _IDLE_STOP_MARKER.exists():
            old_pid = int(_IDLE_STOP_MARKER.read_text().strip() or "0")
            if old_pid > 0:
                subprocess.run(["kill", str(old_pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass

    launcher = ["nohup", "/bin/bash", "-lc", script]
    try:
        with open(os.devnull, "wb") as devnull:
            process = subprocess.Popen(launcher, stdout=devnull, stderr=devnull, start_new_session=True)
        _IDLE_STOP_MARKER.write_text(str(process.pid))
    except Exception as exc:  # pragma: no cover - host dependent.
        logger.debug("Could not schedule Clash idle stop: %s", exc)


@contextlib.contextmanager
def jina_proxy_request_kwargs() -> Iterator[dict[str, dict[str, str]]]:
    """Start the local Clash proxy on demand and return requests kwargs for Jina calls."""
    env = _merged_env()
    if not _proxy_enabled(env):
        yield {}
        return

    proxy_url = _proxy_url(env)
    host, port = _proxy_host_port(proxy_url)
    _ensure_clash_running(env, host, port)
    _mark_last_used()
    try:
        yield {"proxies": {"http": proxy_url, "https": proxy_url}}
    finally:
        _mark_last_used()
        _schedule_idle_stop(env)
