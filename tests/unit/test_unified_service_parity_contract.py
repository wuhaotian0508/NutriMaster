from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "src" / "nutrimaster" / "web"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def test_web_package_has_one_corpus_service_construction_site():
    calls_by_file: dict[str, list[str]] = {}
    for path in WEB_ROOT.rglob("*.py"):
        names = [
            name
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Call)
            and (name := _called_name(node))
            in {"create_services", "WebServices", "JinaRetriever", "ToolRegistry"}
        ]
        if names:
            calls_by_file[str(path.relative_to(WEB_ROOT))] = names

    assert calls_by_file == {
        "app.py": ["create_services"],
        "deps.py": ["JinaRetriever", "ToolRegistry", "WebServices"],
    }


def test_direct_module_entrypoint_reuses_the_already_constructed_app():
    tree = _tree(WEB_ROOT / "app.py")
    uvicorn_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
        and node.func.attr == "run"
    ]

    assert len(uvicorn_calls) == 1
    call = uvicorn_calls[0]
    assert isinstance(call.args[0], ast.Name)
    assert call.args[0].id == "app"
    keyword_values = {keyword.arg: keyword.value for keyword in call.keywords}
    assert isinstance(keyword_values["reload"], ast.Constant)
    assert keyword_values["reload"].value is False
    assert isinstance(keyword_values["workers"], ast.Constant)
    assert keyword_values["workers"].value == 1


def test_unified_fastapi_mounts_all_legacy_and_pi_route_families():
    tree = _tree(WEB_ROOT / "app.py")
    install_routes = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_install_routes"
    )
    mounted_routers = {
        call.args[0].value.id
        for call in ast.walk(install_routes)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "include_router"
        and call.args
        and isinstance(call.args[0], ast.Attribute)
        and call.args[0].attr == "router"
        and isinstance(call.args[0].value, ast.Name)
    }
    assert mounted_routers == {
        "query",
        "pi",
        "experiment",
        "library",
        "admin",
        "auth",
        "system",
    }

    app_source = (WEB_ROOT / "app.py").read_text(encoding="utf-8")
    assert 'app.mount("/admin", WSGIMiddleware(flask_app))' in app_source

    expected_paths = {
        "query.py": {"/api/query", "/api/feedback", "/api/rag/search"},
        "pi.py": {"/api/pi/query", "/api/pi/internal/tools"},
        "experiment.py": {
            "/api/experiment/preview",
            "/api/experiment/run",
            "/api/gene-transfer/preview",
            "/api/gene-transfer/run",
        },
        "library.py": {
            "/api/personal/upload",
            "/api/library/upload",
            "/api/personal/files",
            "/api/library/files",
            "/api/personal/files/{filename}",
            "/api/library/files/{filename}",
            "/api/personal/files/{filename}/rename",
            "/api/library/files/{filename}/rename",
        },
        "admin.py": {
            "/api/skills",
            "/api/skills/{name}",
            "/api/skills/generate",
            "/api/tools",
            "/api/admin/upload_data",
            "/api/admin/reindex_status",
        },
        "auth.py": {
            "/api/auth/signup",
            "/api/auth/verify",
            "/api/auth/resend",
            "/api/user/profile",
            "/api/user/account",
        },
        "system.py": {"/api/health", "/api/config"},
    }
    for filename, expected in expected_paths.items():
        paths = {
            decorator.args[0].value
            for node in ast.walk(_tree(WEB_ROOT / "routes" / filename))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == "router"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
            and isinstance(decorator.args[0].value, str)
        }
        assert expected <= paths

    extraction_admin_paths = {
        decorator.args[0].value
        for node in ast.walk(_tree(WEB_ROOT / "admin" / "app.py"))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "admin_bp"
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
        and isinstance(decorator.args[0].value, str)
    }
    assert {
        "/",
        "/api/config",
        "/api/status",
        "/api/upload",
        "/api/pipeline/settings",
        "/api/pipeline/preview",
        "/api/pipeline/run",
        "/api/pipeline/stream",
        "/api/pipeline/stop",
        "/api/pipeline/output",
        "/api/papers",
        "/api/prompt",
        "/api/schema",
        "/api/pipeline/tokens",
        "/api/index/status",
        "/api/index/rebuild",
    } <= extraction_admin_paths


def test_pi_and_legacy_paths_share_the_host_registry_and_retriever():
    app_source = (WEB_ROOT / "app.py").read_text(encoding="utf-8")
    deps_source = (WEB_ROOT / "deps.py").read_text(encoding="utf-8")
    pi_source = (WEB_ROOT / "routes" / "pi.py").read_text(encoding="utf-8")

    assert "app.state.services = create_services(settings)" in app_source
    assert "return request.app.state.services" in deps_source
    assert "GeneDbSource(retriever)" in deps_source
    assert "agent=Agent(registry=registry" in deps_source
    assert "PiToolService(services.registry)" in pi_source
