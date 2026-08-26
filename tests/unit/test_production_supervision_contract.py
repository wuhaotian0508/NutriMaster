from __future__ import annotations

from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = ROOT / "deploy" / "systemd"


def _unit_directives(name: str) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    current = ""
    for raw_line in (SYSTEMD_DIR / name).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            continue
        key, value = line.split("=", 1)
        sections[current][key].append(value)
    return sections


def test_all_processes_share_a_v1_compatible_aggregate_memory_boundary():
    expected_limits = {
        "nutrimaster-unified.service": "3G",
        "nutrimaster-index-builder.service": "2560M",
        "nutrimaster-pi.service": "768M",
    }
    for unit_name, expected in expected_limits.items():
        service = _unit_directives(unit_name)["Service"]
        assert service["Slice"] == ["nutrimaster.slice"]
        assert service["MemoryAccounting"] == ["true"]
        assert service["MemoryLimit"] == [expected]
        assert service["MemoryMax"] == [expected]

    parent = _unit_directives("nutrimaster.slice")["Slice"]
    assert parent["MemoryLimit"] == ["5632M"]
    assert parent["MemoryMax"] == ["5632M"]

    for unit_name in (
        "nutrimaster-index-bootstrap.service",
        "nutrimaster-index-bootstrap-recovery.service",
    ):
        service = _unit_directives(unit_name)["Service"]
        assert service["Slice"] == ["nutrimaster.slice"]
        assert service["MemoryAccounting"] == ["true"]
        assert service["MemoryLimit"] == ["2560M"]
        assert service["MemoryMax"] == ["2560M"]


def test_shared_slice_oom_priority_biases_away_from_the_live_python_service():
    unified = int(
        _unit_directives("nutrimaster-unified.service")["Service"]["OOMScoreAdjust"][0]
    )
    pi = int(_unit_directives("nutrimaster-pi.service")["Service"]["OOMScoreAdjust"][0])
    builder = int(
        _unit_directives("nutrimaster-index-builder.service")["Service"]["OOMScoreAdjust"][0]
    )
    assert unified < pi < builder


def test_restart_limits_and_builder_ordering_do_not_form_a_cycle():
    for unit_name in (
        "nutrimaster-unified.service",
        "nutrimaster-pi.service",
        "nutrimaster-index-builder.service",
    ):
        unit = _unit_directives(unit_name)
        assert unit["Unit"]["StartLimitIntervalSec"] == ["300"]
        assert unit["Unit"]["StartLimitBurst"] == ["3"]
    for unit_name in ("nutrimaster-unified.service", "nutrimaster-pi.service"):
        assert _unit_directives(unit_name)["Service"]["Restart"] == ["on-failure"]
    for unit_name in (
        "nutrimaster-unified.service",
        "nutrimaster-pi.service",
        "nutrimaster-index-builder.service",
    ):
        assert _unit_directives(unit_name)["Service"]["KillMode"] == ["control-group"]

    unified = _unit_directives("nutrimaster-unified.service")
    assert unified["Service"]["TimeoutStopSec"] == ["360"]

    builder = _unit_directives("nutrimaster-index-builder.service")
    assert builder["Unit"]["After"] == ["network-online.target"]
    assert all(
        "nutrimaster-unified.service" not in value
        for values in builder["Unit"].values()
        for value in values
    )
    assert builder["Service"]["Type"] == ["oneshot"]
    assert builder["Service"]["TimeoutStopSec"] == ["660"]
    assert builder["Service"]["ExecStopPost"] == [
        "/root/code/nutrimaster-current/start-index-builder-production.sh recover-interrupted"
    ]


def test_production_scripts_apply_memory_sensitive_runtime_settings():
    unified = (ROOT / "start-unified-production.sh").read_text(encoding="utf-8")
    builder = (ROOT / "start-index-builder-production.sh").read_text(encoding="utf-8")
    pi = (ROOT / "start-pi-production.sh").read_text(encoding="utf-8")
    cli = (ROOT / "src" / "nutrimaster" / "cli.py").read_text(encoding="utf-8")
    admin = (ROOT / "src" / "nutrimaster" / "web" / "admin" / "app.py").read_text(
        encoding="utf-8"
    )

    assert "export WEB_CONCURRENCY=1" in unified
    assert "export NUTRIMASTER_RAG_MAX_CONCURRENT_SEARCHES=1" in unified
    assert "export NUTRIMASTER_PIPELINE_DEFAULT_WORKERS=1" in unified
    assert "export NUTRIMASTER_PIPELINE_MAX_WORKERS=1" in unified
    assert "export NUTRIMASTER_EXTRACTION_MAX_MARKDOWN_BYTES=16777216" in unified
    assert "export NUTRIMASTER_PERSONAL_LIBRARY_CACHE_SIZE=16" in unified
    assert "export NUTRIMASTER_PERSONAL_LIBRARY_CACHE_TTL_SECONDS=900" in unified
    assert "export NUTRIMASTER_PI_TURN_TIMEOUT_SECONDS=300" in unified
    assert "export NUTRIMASTER_DISABLE_BM25=0" in builder
    assert "export NUTRIMASTER_ENABLE_FIELD_KEYWORD=1" in builder
    assert "export NUTRIMASTER_UNIFIED_WEB_PORT=5000" in builder
    assert "export NUTRIMASTER_INDEX_ACTIVATION_TIMEOUT_SECONDS=120" in builder
    assert "workers=1" in cli
    assert '"NUTRIMASTER_PIPELINE_DEFAULT_WORKERS"' in admin
    assert '"NUTRIMASTER_PIPELINE_MAX_WORKERS"' in admin
    assert "exec node src/server.js" in pi
    assert "exec npm start" not in pi
    assert "export NUTRIMASTER_PI_TURN_TIMEOUT_SECONDS=300" in pi
    assert 'export NODE_OPTIONS="--max-old-space-size=512"' in pi
    assert "${NODE_OPTIONS" not in pi
    for source in (unified, pi):
        assert 'EXPECTED_MODEL="deepseek-v4-flash"' in source
        assert '"${MAIN_MODEL:-}" != "$EXPECTED_MODEL"' in source
        assert '"$NUTRIMASTER_PI_MODEL" != "$EXPECTED_MODEL"' in source


def test_production_ports_are_fixed_and_old_python_services_must_be_stopped_first():
    unified = (ROOT / "start-unified-production.sh").read_text(encoding="utf-8")
    builder = (ROOT / "start-index-builder-production.sh").read_text(encoding="utf-8")
    pi = (ROOT / "start-pi-production.sh").read_text(encoding="utf-8")
    nginx = (ROOT / "deploy" / "nginx" / "nutrimaster-unified.conf").read_text(
        encoding="utf-8"
    )

    assert "export WEB_HOST=127.0.0.1" in unified
    assert "export WEB_PORT=5000" in unified
    assert "export NUTRIMASTER_UNIFIED_WEB_PORT=5000" in unified
    assert "export NUTRIMASTER_PI_PORT=8787" in unified
    assert 'export NUTRIMASTER_PI_RUNTIME_URL="http://127.0.0.1:8787"' in unified
    assert "export NUTRIMASTER_PI_HOST=127.0.0.1" in pi
    assert "export NUTRIMASTER_PI_PORT=8787" in pi
    assert 'export NUTRIMASTER_PI_AGENT_DIR="$SCRIPT_DIR/pi-runtime/.pi-agent"' in pi
    assert "${NUTRIMASTER_PI_PORT" not in unified
    assert "${NUTRIMASTER_PI_PORT" not in pi
    assert "export NUTRIMASTER_UNIFIED_WEB_PORT=5000" in builder

    assert "server 127.0.0.1:5000;" in nginx
    assert "listen 127.0.0.1:5080;" in nginx
    assert "nutrimaster_bohrium_agent_rate" in nginx
    assert "location ^~ /api/pi/internal/" in nginx

    socket = (ROOT / "deploy" / "systemd" / "nutrimaster-bohrium-proxy.socket").read_text(
        encoding="utf-8"
    )
    proxy = (ROOT / "deploy" / "systemd" / "nutrimaster-bohrium-proxy.service").read_text(
        encoding="utf-8"
    )
    assert "ListenStream=172.17.4.12:5000" in socket
    assert "After=nutrimaster-unified.service" in socket
    assert "PartOf=nutrimaster-unified.service" in socket
    assert "systemd-socket-proxyd 127.0.0.1:5080" in proxy

    # The guards are exact, read-only listener queries. They fail closed when
    # ss is unavailable/fails and ask the operator to stop the known owners;
    # they never guess a PID or kill a process.
    assert "-H -ltn 'sport = :5000'" in unified
    assert "-H -ltn 'sport = :5002'" in unified
    assert 'if [[ -z "$SS_BIN" ]]' in unified
    assert 'if ! OLD_5000_LISTENERS=' in unified
    assert 'if ! LEGACY_5002_LISTENERS=' in unified
    assert "stop and disable the known old 5000 service" in unified
    assert "stop and disable the known old 5002 service" in unified
    assert unified.index("'sport = :5000'") < unified.index("verify-active")
    assert unified.index("'sport = :5002'") < unified.index("verify-active")
    assert "pkill" not in unified
    assert "killall" not in unified
    assert "fuser" not in unified


def test_proxy_environment_and_public_pi_boundary_are_locked_down():
    scripts = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "start-unified-production.sh",
            "start-index-builder-production.sh",
            "start-pi-production.sh",
        )
    }
    proxy_unset = (
        "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy "
        "NO_PROXY no_proxy"
    )
    for source in scripts.values():
        assert proxy_unset in source

    assert "unset NODE_TLS_REJECT_UNAUTHORIZED NODE_USE_ENV_PROXY" in scripts[
        "start-pi-production.sh"
    ]
    assert "export FORWARDED_ALLOW_IPS=127.0.0.1" in scripts[
        "start-unified-production.sh"
    ]
    assert "/usr/bin/curl -q --noproxy '*'" in scripts["start-unified-production.sh"]

    nginx = (ROOT / "deploy" / "nginx" / "nutrimaster-unified.conf").read_text(
        encoding="utf-8"
    )
    exact_internal = nginx.split("location = /api/pi/internal {", 1)[1].split("}", 1)[0]
    nested_internal = nginx.split("location ^~ /api/pi/internal/ {", 1)[1].split(
        "}", 1
    )[0]
    assert "return 404;" in exact_internal
    assert "proxy_pass" not in exact_internal
    assert "return 404;" in nested_internal
    assert "proxy_pass" not in nested_internal
    assert nginx.index("location = /api/pi/internal {") < nginx.index("location / {")
    assert "$proxy_add_x_forwarded_for" not in nginx
    assert nginx.count("proxy_set_header X-Forwarded-For $remote_addr;") == 4


def test_web_startup_refuses_inline_index_construction():
    unified = (ROOT / "start-unified-production.sh").read_text(encoding="utf-8")
    deps = (ROOT / "src" / "nutrimaster" / "web" / "deps.py").read_text(encoding="utf-8")

    assert "export NUTRIMASTER_WEB_BUILD_INDEX=0" in unified
    assert "NUTRIMASTER_WEB_BUILD_INDEX is forbidden" in deps
    assert "retriever.build_index(" not in deps
