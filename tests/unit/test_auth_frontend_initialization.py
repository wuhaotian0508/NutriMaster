from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
AUTH_JS = ROOT / "src" / "nutrimaster" / "web" / "static" / "auth.js"


def _run_node_harness(harness: str) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is required for the browser-auth JavaScript harness")

    source = AUTH_JS.read_text(encoding="utf-8")
    script = f"""
const vm = require('vm');
const assert = require('assert');
const context = {{ console, setTimeout, clearTimeout, assert, process }};
context.globalThis = context;
vm.createContext(context);
vm.runInContext({json.dumps(source)}, context, {{ filename: 'auth.js' }});
vm.runInContext({json.dumps(harness)}, context, {{ filename: 'auth-harness.js' }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_login_waits_for_the_in_flight_auth_initialization() -> None:
    result = _run_node_harness(
        """
(async () => {
    let releaseConfig;
    let createCount = 0;
    let signInCount = 0;
    const configGate = new Promise(resolve => { releaseConfig = resolve; });
    globalThis.fetch = async () => {
        await configGate;
        return {
            ok: true,
            json: async () => ({
                supabase_url: 'https://example.supabase.co',
                supabase_anon_key: 'anon-key',
            }),
        };
    };
    globalThis.supabase = {
        createClient: () => {
            createCount += 1;
            return {
                auth: {
                    onAuthStateChange: () => {},
                    signInWithPassword: async () => {
                        signInCount += 1;
                        return { data: { session: {} }, error: null };
                    },
                },
            };
        },
    };

    const initialization = initAuth();
    const login = loginWithEmail('user@example.com', 'password');
    await Promise.resolve();
    assert.strictEqual(signInCount, 0);
    releaseConfig();
    await Promise.all([initialization, login]);
    assert.strictEqual(createCount, 1);
    assert.strictEqual(signInCount, 1);
    console.log(JSON.stringify({ createCount, signInCount }));
})().catch(error => { console.error(error); process.exit(1); });
"""
    )

    assert result == {"createCount": 1, "signInCount": 1}


def test_login_reports_initialization_failure_instead_of_null_auth_error() -> None:
    result = _run_node_harness(
        """
(async () => {
    globalThis.fetch = async () => ({
        ok: true,
        json: async () => ({ supabase_url: '', supabase_anon_key: '' }),
    });
    globalThis.supabase = { createClient: () => null };
    globalThis.document = { getElementById: () => null };

    let message = '';
    try {
        await loginWithEmail('user@example.com', 'password');
    } catch (error) {
        message = error.message;
    }
    assert.match(message, /^认证服务初始化失败：/);
    assert.ok(!message.includes("null (reading 'auth')"));
    console.log(JSON.stringify({ message }));
})().catch(error => { console.error(error); process.exit(1); });
"""
    )

    assert result["message"].startswith("认证服务初始化失败：")
