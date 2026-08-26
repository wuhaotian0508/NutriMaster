from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_frontend_can_switch_between_legacy_and_pi_query_endpoints():
    app_js = (ROOT / "src/nutrimaster/web/static/app.js").read_text()
    index_html = (ROOT / "src/nutrimaster/web/static/index.html").read_text()

    assert 'id="pi-runtime-btn"' in index_html
    assert "const PI_RUNTIME_PREFERENCE_KEY = 'nutrimasterPiRuntimePreference';" in app_js
    assert "let usePiRuntime = localStorage.getItem(PI_RUNTIME_PREFERENCE_KEY) !== 'false';" in app_js
    assert "const endpoint = usePiRuntime ? '/api/pi/query' : '/api/query';" in app_js
    pi_payload = app_js.split("const payload = usePiRuntime", 1)[1].split(": {", 1)[0]
    assert "session_id: currentSessionId || ''" in pi_payload
    assert "client_turn_id: messageId" in pi_payload
    assert "capture_consent: true" in pi_payload
