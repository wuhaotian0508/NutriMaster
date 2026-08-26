from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from typing import Mapping

from nutrimaster.web.responses import error_response
from nutrimaster.config.settings import Settings

SERVICE_NAME = "nutrimaster"


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    创建 nutrimaster CLI 的参数解析器，包含以下子命令：
    - serve: 启动服务（可选 --check-config 检查配置）
    - check-config: 仅检查配置是否完整
    - web: 启动 Web 服务器（可指定 host、port、reload）
    - extract: 运行基因信息提取流程（可指定 test、workers、rerun）

    Returns:
        argparse.ArgumentParser: 配置好的命令行参数解析器实例。
    """
    parser = argparse.ArgumentParser(prog=SERVICE_NAME)
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--check-config", action="store_true")

    subparsers.add_parser("check-config")

    web = subparsers.add_parser("web")
    web.add_argument("--host")
    web.add_argument("--port", type=int)
    web.add_argument("--reload", action="store_true")

    extract = subparsers.add_parser("extract")
    extract.add_argument("--test")
    extract.add_argument("--workers", type=int)
    extract.add_argument("--rerun", action="store_true")

    return parser


def _print_json(payload: dict) -> None:
    """将字典以 JSON 格式打印到标准输出。

    Args:
        payload: 要序列化并打印的字典对象。
    """
    print(json.dumps(payload, ensure_ascii=False))


def _check_config(env: Mapping[str, str] | None = None, *, mode: str = "check-config") -> int:
    """检查服务配置是否完整。

    从环境变量中加载配置，验证所有必需的服务密钥是否已设置。
    如果存在缺失的配置项，会打印错误信息的 JSON 并返回非零退出码；
    如果配置完整，打印成功状态的 JSON。

    Args:
        env: 可选的环境变量映射，用于覆盖系统环境变量。默认为 None，使用系统环境变量。
        mode: 检查模式的名称，用于输出信息中标识调用来源。默认为 "check-config"。

    Returns:
        int: 退出码。0 表示配置完整，非零值表示存在缺失的配置项。
    """
    settings = Settings.from_env(env)
    missing = settings.missing_real_service_keys()
    if missing:
        response = error_response(
            code="missing_real_service_config",
            message="NutriMaster real-service configuration is incomplete",
            status_code=2,
            details={"missing": missing},
        )
        _print_json(response.body)
        return response.status_code

    _print_json({"status": "ok", "service": SERVICE_NAME, "mode": mode})
    return 0


def _run_web(args: argparse.Namespace) -> int:
    """启动 Web 服务器。

    根据命令行参数和配置文件中的 RAG 设置，使用 uvicorn 启动 nutrimaster Web 应用。
    命令行参数优先级高于配置文件中的默认值。

    Args:
        args: 解析后的命令行参数，可包含 host、port 和 reload 选项。

    Returns:
        int: 退出码，始终返回 0。
    """
    import uvicorn

    settings = Settings.from_env()
    rag = settings.rag
    host = args.host or (rag.web_host if rag else "0.0.0.0")
    port = args.port or (rag.web_port if rag else 5000)
    reload = args.reload or (rag.debug if rag else False)
    raw_limit = os.getenv("NUTRIMASTER_WEB_LIMIT_CONCURRENCY", "")
    limit_concurrency = None
    if raw_limit:
        try:
            limit_concurrency = int(raw_limit)
        except ValueError as exc:
            raise RuntimeError("NUTRIMASTER_WEB_LIMIT_CONCURRENCY must be an integer") from exc
        if not 1 <= limit_concurrency <= 1024:
            raise RuntimeError("NUTRIMASTER_WEB_LIMIT_CONCURRENCY must be between 1 and 1024")
    uvicorn.run(
        "nutrimaster.web.app:app",
        host=host,
        port=port,
        reload=reload,
        workers=1,
        limit_concurrency=limit_concurrency,
    )
    return 0


def _run_extract(args: argparse.Namespace) -> int:
    """运行基因信息提取流程。

    调用 ExtractionService 执行从学术文献中提取作物营养代谢基因信息的流程。
    处理完成后将结果（包括文件数、已处理数、失败数、跳过数等统计信息）以 JSON 格式输出。

    Args:
        args: 解析后的命令行参数，可包含以下选项：
            - test: 测试模式标识。
            - workers: 并行工作线程数。
            - rerun: 是否强制重新运行已处理过的文件。

    Returns:
        int: 退出码，始终返回 0。
    """
    from nutrimaster.extraction.service import ExtractionService

    if args.rerun:
        os.environ["FORCE_RERUN"] = "1"
    result = ExtractionService().run(
        test=args.test,
        workers=args.workers,
        report_prefix="cli-extract",
    )
    _print_json(
        {
            "status": "ok",
            "files": result.files,
            "processed": result.processed,
            "failed": result.failed,
            "skipped": result.skipped,
            "stopped": result.stopped,
            "token_report": result.token_report,
        }
    )
    return 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """NutriMaster CLI 的主入口函数。

    解析命令行参数并根据子命令分发到对应的处理函数：
    - serve --check-config: 在 serve 模式下检查配置
    - check-config: 检查配置是否完整
    - web: 启动 Web 服务器
    - extract: 运行基因信息提取流程

    如果子命令未匹配任何已知操作，则打印帮助信息。

    Args:
        argv: 可选的命令行参数序列。默认为 None，使用 sys.argv。
        env: 可选的环境变量映射。默认为 None，使用系统环境变量。

    Returns:
        int: 退出码。0 表示成功，2 表示未知命令。
    """
    args = _build_parser().parse_args(argv)

    if args.command == "serve" and args.check_config:
        return _check_config(env, mode="serve")
    if args.command == "check-config":
        return _check_config(env, mode="check-config")
    if args.command == "web":
        return _run_web(args)
    if args.command == "extract":
        return _run_extract(args)

    _build_parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
