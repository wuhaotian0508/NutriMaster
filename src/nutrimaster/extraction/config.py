"""
config.py — 提取管线的集中配置模块。

包含所有路径配置、API 设置和并发参数。
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ─── Path configuration ──────────────────────────────────────────────────────
EXTRACTOR_DIR = Path(__file__).parent
BASE_DIR = Path(os.getenv("BASE_DIR", EXTRACTOR_DIR.parents[2]))
PROMPTS_DIR = EXTRACTOR_DIR / "prompts"
INPUT_DIR = Path(os.getenv("MD_DIR", EXTRACTOR_DIR / "input"))
OUTPUT_DIR = Path(os.getenv("JSON_DIR", BASE_DIR / "data" / "corpus"))
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", EXTRACTOR_DIR / "reports"))
TOKEN_USAGE_DIR = Path(os.getenv("TOKEN_USAGE_DIR", EXTRACTOR_DIR / "reports" / "token-usage"))
PROCESSED_DIR = Path(os.getenv("PROCESSED_DIR", INPUT_DIR / "processed"))

PROMPT_PATH = Path(os.getenv("PROMPT_PATH", PROMPTS_DIR / "nutri_gene_prompt_v5.txt"))
SCHEMA_PATH = Path(os.getenv("SCHEMA_PATH", PROMPTS_DIR / "nutri_gene_schema_v5.json"))

# ─── API configuration ───────────────────────────────────────────────────────
EXTRACTOR_MODEL = os.getenv("EXTRACTOR_MODEL", "")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))

# ─── Concurrency ─────────────────────────────────────────────────────────────
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))


def _bounded_markdown_bytes() -> int:
    """Return a fail-fast byte limit for one extraction input document."""

    hard_max = 32 * 1024 * 1024
    raw = os.getenv("NUTRIMASTER_EXTRACTION_MAX_MARKDOWN_BYTES", str(16 * 1024 * 1024))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "NUTRIMASTER_EXTRACTION_MAX_MARKDOWN_BYTES must be an integer"
        ) from exc
    if not 1 <= value <= hard_max:
        raise RuntimeError(
            "NUTRIMASTER_EXTRACTION_MAX_MARKDOWN_BYTES must be between 1 and 32 MiB"
        )
    return value


MAX_MARKDOWN_BYTES = _bounded_markdown_bytes()


def read_markdown_bounded(path: Path) -> str:
    """Read one UTF-8 Markdown file without allocating an unbounded string."""

    source = Path(path)
    with source.open("rb") as file:
        payload = file.read(MAX_MARKDOWN_BYTES + 1)
    if len(payload) > MAX_MARKDOWN_BYTES:
        raise ValueError(
            f"Markdown file exceeds the {MAX_MARKDOWN_BYTES}-byte extraction limit: "
            f"{source.name}"
        )
    return payload.decode("utf-8")


_primary_client = None


def get_openai_client():
    """获取 extraction 专用 OpenAI 客户端（单例缓存）。

    使用环境变量 OPENAI_API_KEY 和 OPENAI_BASE_URL 初始化客户端。
    整个进程只创建一次，后续调用返回同一实例。

    Returns:
        OpenAI: 配置好的 OpenAI 客户端实例
    """
    global _primary_client
    if _primary_client is None:
        _primary_client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
    return _primary_client


def ensure_dirs():
    """创建所有必需的输出目录（输出、报告、token 用量、已处理）。

    在 pipeline 启动时调用一次，确保目录存在。
    """
    for d in [OUTPUT_DIR, REPORTS_DIR, TOKEN_USAGE_DIR, PROCESSED_DIR]:
        d.mkdir(parents=True, exist_ok=True)
