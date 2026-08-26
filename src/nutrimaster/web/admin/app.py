"""
 NutriMaster Admin Panel 后端（Flask Blueprint，挂载在主 Web 服务下）

功能概述：
  1. ZIP 上传 → 递归解压 .md 到 src/nutrimaster/extraction/input/，自动去重
  2. Pipeline 预览 / 批量运行（ThreadPoolExecutor 并行）/ 停止
  3. SSE 流式推送处理进度
  4. Console Output 实时查看 pipeline 的 print 输出
  5. 在线编辑 prompt 和 schema 文件
  6. 可调参数：temperature、verify 批次大小、并行 worker 数

认证：Supabase Bearer Token + ADMIN_EMAIL 邮箱白名单
"""

import json
import io
import os
import queue
import threading
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Response, jsonify, request, send_from_directory

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  环境变量设置 — 必须在 import nutrimaster.extraction 之前完成                            ║
# ║  因为 src/nutrimaster/extraction/config.py 在 import 时就读取 env，之后无法再改              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

from nutrimaster.config.settings import Settings

SETTINGS = Settings.from_env()
REPO_ROOT = SETTINGS.project_root

# 设置 extractor 需要的关键路径（setdefault 不会覆盖已存在的值）
os.environ.setdefault("JSON_DIR", str(SETTINGS.rag.data_dir))
EXTRACTION_ROOT = REPO_ROOT / "src" / "nutrimaster" / "extraction"
os.environ.setdefault("PROMPT_PATH", str(EXTRACTION_ROOT / "prompts" / "nutri_gene_prompt_v5.txt"))
os.environ.setdefault("SCHEMA_PATH", str(EXTRACTION_ROOT / "prompts" / "nutri_gene_schema_v5.json"))
os.environ.setdefault("MD_DIR", str(EXTRACTION_ROOT / "input"))

# 加载 .env 文件（API key、Supabase 等配置）
from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from nutrimaster.web.admin.upload import (
    ZipUploadError,
    ZipUploadLimitError,
    ZipUploadStorageError,
    extract_zip_upload,
)

# Supabase 客户端（用于服务端验证 token）
from supabase import create_client

# extractor 模块导入（此时 env 已就绪）
from nutrimaster.extraction.config import INPUT_DIR, ensure_dirs
from nutrimaster.extraction.pipeline import process_one_paper, run_pipeline_batch, save_token_report
from nutrimaster.extraction.token_tracker import TokenTracker
from nutrimaster.experiment.service import ExperimentBusyError, ExperimentExecutionGate

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Flask App 初始化                                                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝

from flask import Blueprint

# Blueprint 版本（供 nutrimaster.web.app 集成使用）
# url_prefix 由注册方决定，这里不设前缀
admin_bp = Blueprint(
    "admin",
    __name__,
    static_folder="static",
    static_url_path="/static",
)

# 下方所有路由都注册到 admin_bp 上，由主 FastAPI app 挂载。

# 管理员邮箱白名单（逗号分隔，支持多个邮箱）
ADMIN_EMAILS = {e.strip() for e in os.getenv("ADMIN_EMAIL", "").split(",") if e.strip()}

# Supabase 配置
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# 常用路径
DATA_DIR = SETTINGS.rag.data_dir                  # verified JSON 最终存放目录
PROMPT_PATH = Path(os.environ["PROMPT_PATH"])     # prompt 文件路径
SCHEMA_PATH = Path(os.environ["SCHEMA_PATH"])     # schema 文件路径

# 初始化 Supabase 服务端客户端（用 service_role_key 验证用户 token）
supabase = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except MemoryError:
        raise
    except Exception as e:
        print(f"⚠️  Supabase 初始化失败 ({e})，认证功能不可用")

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Pipeline 参数默认值 & 允许范围                                            ║
# ║  前端 Settings 面板可调，通过 POST body 传入                               ║
# ╚══════════════════════════════════════════════════════════════════════════╝


def _pipeline_worker_env(name: str, default: int, *, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


_PIPELINE_MAX_WORKERS = _pipeline_worker_env(
    "NUTRIMASTER_PIPELINE_MAX_WORKERS",
    1,
    maximum=1,
)
_PIPELINE_DEFAULT_WORKERS = _pipeline_worker_env(
    "NUTRIMASTER_PIPELINE_DEFAULT_WORKERS",
    1,
    maximum=_PIPELINE_MAX_WORKERS,
)

PIPELINE_DEFAULTS = {
    "temperature": 0.7,          # 提取 API 的温度参数（0=确定性，1=创造性）
    "verify_batch_genes": 12,    # 每批验证多少个基因（越大单次 API 处理越多）
    "max_workers": _PIPELINE_DEFAULT_WORKERS,
}

PIPELINE_LIMITS = {
    "temperature":       (0.0, 1.0),
    "verify_batch_genes": (1, 50),
    "max_workers":        (1, _PIPELINE_MAX_WORKERS),
}

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Pipeline 运行状态（模块级，线程安全）                                       ║
# ║  同一时间只允许一个 pipeline 实例运行                                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝

_lock = threading.Lock()
# Serialize the check-and-start section and synchronous preview execution.
# ``pipeline_state['running']`` alone is not an atomic reservation: two WSGI
# threads could both observe False before either starts a worker.
_pipeline_execution_gate = ExperimentExecutionGate()
pipeline_state = {
    "running": False,          # 是否有 pipeline 正在运行
    "stop_requested": False,   # 用户是否请求停止
    "total": 0,                # 本次运行总文件数
    "done": 0,                 # 已完成文件数
    "current_file": "",        # 当前正在处理的文件名
    "events": queue.Queue(),   # SSE 事件队列（元组：(event_type, data_dict)）
    "thread": None,            # 后台线程引用
    "output": "",              # 最近一次运行的 console 输出（最后 5000 字符）
    "_tee": None,              # 运行中的 _TeeWriter 实例（用于实时读取输出）
    "tracker": None,           # 当前运行的 TokenTracker 实例（供实时查询）
}

_index_refresh_handler: Callable[[Path, bool], dict] | None = None
_index_status_handler: Callable[[], dict] | None = None
_index_build_status_handler: Callable[[], dict] | None = None


def configure_pipeline_execution_gate(gate: ExperimentExecutionGate | None) -> None:
    """Share the host's high-memory execution gate with Admin extraction.

    Preview owns the lease synchronously. A batch transfers its lease to the
    background worker so experiments remain excluded for the real worker
    lifetime, not merely for the HTTP start request.
    """
    global _pipeline_execution_gate
    _pipeline_execution_gate = gate or ExperimentExecutionGate()


def configure_index_refresh(handler: Callable[[Path, bool], dict] | None) -> None:
    """注入宿主应用的独立索引构建排队函数。

    无论独立或集成运行，本模块都只提交持久化任务；它绝不会在
    Web 进程中创建检索器或构建语料规模的索引。

    参数:
        handler: 索引刷新回调函数，接受 (data_dir: Path, force: bool) 两个参数。
                 传入 None 表示清除已注入的处理器，回退到默认行为。
    """
    global _index_refresh_handler
    _index_refresh_handler = handler


def configure_index_status(handler: Callable[[], dict] | None) -> None:
    """Inject a status reader for the already-loaded canonical retriever."""
    global _index_status_handler
    _index_status_handler = handler


def configure_index_build_status(handler: Callable[[], dict] | None) -> None:
    """Inject the durable builder status reader owned by the host app."""

    global _index_build_status_handler
    _index_build_status_handler = handler


def _refresh_index(data_dir: Path, *, force: bool = False) -> dict:
    """Queue an index build in the isolated builder service.

    A return value means the request is durably queued, not that a generation
    has already been built or activated.

    参数:
        data_dir: 语料 JSON 文件所在目录路径。
        force: 是否强制重建索引（默认 False，仅增量更新）。
    """
    if _index_refresh_handler is not None:
        return _index_refresh_handler(Path(data_dir), force)

    from nutrimaster.rag.index_build_jobs import IndexBuildQueue

    if SETTINGS.rag is None or Path(data_dir).resolve() != SETTINGS.rag.data_dir.resolve():
        raise RuntimeError("index builder only accepts the configured corpus directory")
    return IndexBuildQueue.from_settings(SETTINGS).enqueue(
        force=force,
        reason="standalone-admin",
    )


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  _TeeWriter — 同时写入 stdout 和内存缓冲区                                 ║
# ║  用于捕获 process_one_paper 的 print 输出，不影响终端显示                     ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class _TeeWriter:
    """将写入内容同时输出到原始流和内存缓冲区（线程安全）。

    用于捕获 process_one_paper 的 print 输出，不影响终端正常显示。
    内部维护一个有最大字符数限制的 StringIO 缓冲区，防止长时间运行导致内存溢出。
    """

    def __init__(self, original, max_chars=200_000):
        """初始化 TeeWriter。

        参数:
            original: 原始输出流（如 sys.__stdout__），写入内容会同时发送到该流。
            max_chars: 内存缓冲区最大字符数（默认 200,000），超出后自动截取尾部。
        """
        self._original = original    # 原始 stdout/stderr
        self._buf = io.StringIO()    # 内存缓冲区
        self._max = max_chars        # 缓冲区最大字符数（防止内存爆炸）
        self._lock = threading.Lock()

    def write(self, s):
        """将内容同时写入原始流和内存缓冲区。

        当缓冲区超过最大字符数限制时，自动截取尾部保留。

        参数:
            s: 要写入的字符串内容。

        返回:
            int: 写入的字符数。
        """
        self._original.write(s)
        with self._lock:
            self._buf.write(s)
            # 超过上限时截取尾部
            if self._buf.tell() > self._max:
                text = self._buf.getvalue()[-self._max:]
                self._buf = io.StringIO()
                self._buf.write(text)
        return len(s)

    def flush(self):
        """刷新原始输出流的缓冲区。"""

    def get_tail(self, n=5000):
        """获取缓冲区末尾指定数量的字符。

        供 /api/pipeline/output 接口返回最近的控制台输出。

        参数:
            n: 要获取的最大字符数（默认 5000）。

        返回:
            str: 缓冲区末尾最多 n 个字符的文本。
        """
        with self._lock:
            text = self._buf.getvalue()
            return text[-n:] if len(text) > n else text


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  认证装饰器                                                               ║
# ║  login_required: 验证 Supabase Bearer Token                              ║
# ║  admin_required: 额外检查邮箱 == ADMIN_EMAIL                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def _extract_token() -> str:
    """从 HTTP 请求中提取认证 Token。

    优先从 Authorization header 中提取 Bearer Token；
    若 header 中不存在，则从 URL 查询参数 ?token=xxx 中获取。
    SSE 端点（EventSource）不支持自定义 header，因此通过查询参数传递。

    返回:
        str: 提取到的 token 字符串，未找到时返回空字符串。
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.args.get("token", "")


def login_required(f):
    """登录认证装饰器。

    验证请求中的 Supabase access token 是否有效。
    验证成功后将 user 对象挂载到 request.user，供后续处理函数使用。
    若 token 缺失或无效，返回 401 错误；若 Supabase 未配置，返回 500 错误。

    参数:
        f: 被装饰的视图函数。

    返回:
        function: 包装后的视图函数，在调用前先完成 token 验证。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        """验证用户登录 token 并注入用户信息到 request。"""
        token = _extract_token()
        if not token:
            return jsonify({"error": "未登录"}), 401
        if not supabase:
            return jsonify({"error": "Supabase 未配置"}), 500
        try:
            resp = supabase.auth.get_user(token)
            request.user = resp.user
        except MemoryError:
            raise
        except Exception:
            return jsonify({"error": "认证失败，请重新登录"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """管理员权限认证装饰器。

    在 login_required 的基础上，额外检查用户邮箱是否在 ADMIN_EMAILS 白名单中。
    若 ADMIN_EMAIL 环境变量未配置，返回 500 错误；若用户不在白名单中，返回 403 错误。

    参数:
        f: 被装饰的视图函数。

    返回:
        function: 包装后的视图函数，在调用前先完成登录验证和管理员权限校验。
    """
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        """校验当前用户是否在管理员白名单中。"""
        if not ADMIN_EMAILS:
            return jsonify({"error": "ADMIN_EMAIL 未配置"}), 500
        if request.user.email not in ADMIN_EMAILS:
            return jsonify({"error": "仅管理员可访问"}), 403
        return f(*args, **kwargs)
    return decorated


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  辅助函数                                                                ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def get_processed_stems() -> set:
    """扫描语料目录，获取已处理文件的 stem（文件名主干）集合。

    用于上传时去重判断。例如文件 MinerU_markdown_PMC123_nutri_plant_verified.json
    对应的 stem 为 "MinerU_markdown_PMC123"。

    返回:
        set: 已处理文件的 stem 字符串集合。
    """
    return {
        f.name.replace("_nutri_plant_verified.json", "")
        for f in DATA_DIR.glob("*_nutri_plant_verified.json")
    }


def get_input_files() -> list:
    """获取待处理输入目录中所有 Markdown 文件的文件名列表。

    扫描 src/nutrimaster/extraction/input/ 目录，返回所有 .md 后缀文件的文件名（已排序）。

    返回:
        list: 排序后的 .md 文件名字符串列表。若目录不存在则返回空列表。
    """
    if not INPUT_DIR.exists():
        return []
    return sorted(f for f in os.listdir(INPUT_DIR) if f.endswith(".md"))


def _sse_event(event: str, data: dict) -> str:
    """将事件类型和数据格式化为 SSE（Server-Sent Events）协议字符串。

    参数:
        event: SSE 事件类型名称（如 "start"、"processing"、"complete"）。
        data: 事件携带的数据字典，会被序列化为 JSON。

    返回:
        str: 符合 SSE 协议的事件字符串，格式为 "event: xxx\\ndata: {...}\\n\\n"。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _apply_settings(settings: dict):
    """将前端传入的运行参数应用到 extractor 模块的运行时变量。

    由于 extractor 的 config 是模块级导入（import 时复制值），
    因此需要直接修改目标模块的变量才能在运行时生效。

    支持的参数:
      - temperature: 提取 API 的温度参数，会修改 extract.TEMPERATURE
      - verify_batch_genes: 每批验证的基因数量，会修改 verify.GENES_PER_BATCH

    参数:
        settings: 包含参数键值对的字典，键名对应 PIPELINE_DEFAULTS 中的参数名。
                  值会被裁剪到 PIPELINE_LIMITS 定义的允许范围内。
    """
    import nutrimaster.extraction.extract as _ext
    import nutrimaster.extraction.verify as _ver

    # 提取温度（extract.py 第 185 行用 TEMPERATURE 变量）
    if "temperature" in settings:
        val = max(PIPELINE_LIMITS["temperature"][0],
                  min(PIPELINE_LIMITS["temperature"][1], float(settings["temperature"])))
        _ext.TEMPERATURE = val

    # 验证批次基因数（verify.py 的 GENES_PER_BATCH 模块变量）
    if "verify_batch_genes" in settings:
        val = max(PIPELINE_LIMITS["verify_batch_genes"][0],
                  min(PIPELINE_LIMITS["verify_batch_genes"][1], int(settings["verify_batch_genes"])))
        _ver.GENES_PER_BATCH = val



# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  路由：静态文件 & 配置                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@admin_bp.route("/")
def index():
    """返回 Admin SPA（单页应用）的首页 HTML 文件。

    返回:
        Response: 包含 index.html 内容的 Flask 文件响应。
    """
    return send_from_directory(str(Path(__file__).parent / "static"), "index.html")


@admin_bp.route("/api/config")
def api_config():
    """返回前端所需的 Supabase 公钥配置信息。

    该接口无需认证，供前端初始化 Supabase 客户端时使用。

    返回:
        JSON: 包含 supabase_url 和 supabase_anon_key 的 JSON 响应。
    """
    return jsonify({
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": SUPABASE_ANON_KEY,
    })


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  路由：Dashboard 状态                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@admin_bp.route("/api/status")
@admin_required
def api_status():
    """返回管理面板仪表盘的统计数据和 Pipeline 运行状态。

    统计内容包括：待处理队列文件数、已处理论文总数、归档数、
    Pipeline 是否正在运行以及当前进度。

    返回:
        JSON: 包含 input_queue、processed_total、waitlist、
              pipeline_running、pipeline_done、pipeline_total 的 JSON 响应。
    """
    input_count = len(get_input_files())
    processed_count = len(list(DATA_DIR.glob("*_nutri_plant_verified.json")))
    # "Archive" = 已处理过的 .md（被移入 processed 目录）
    waitlist_dir = REPO_ROOT / "extractor" / "input" / "processed"
    waitlist_count = len(list(waitlist_dir.glob("*.md"))) if waitlist_dir.exists() else 0

    with _lock:
        running = pipeline_state["running"]
        done = pipeline_state["done"]
        total = pipeline_state["total"]

    return jsonify({
        "input_queue": input_count,
        "processed_total": processed_count,
        "waitlist": waitlist_count,
        "pipeline_running": running,
        "pipeline_done": done,
        "pipeline_total": total,
    })


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  路由：ZIP 上传                                                           ║
# ║  递归解压所有嵌套 zip → 提取 .md → 去重后存入 src/nutrimaster/extraction/input/               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@admin_bp.route("/api/upload", methods=["POST"])
@admin_required
def api_upload():
    """处理 ZIP 文件上传，递归解压并将 Markdown 文件导入待处理队列。

    从上传的 ZIP 文件中递归解压所有嵌套的 ZIP 和 .md 文件，
    去重后将新文件存入 src/nutrimaster/extraction/input/ 目录。

    去重规则:
      1. stem 已在 data/ 目录中有 verified JSON → 跳过（已在库中）
      2. stem 已在 input/ 目录中 → 跳过（已在队列中）

    返回:
        JSON: 包含 new_files（新增文件列表）、skipped_processed（因已处理而跳过的列表）、
              skipped_existing（因已在队列中而跳过的列表）。
              若无文件上传或 ZIP 中无 .md 文件，返回 400 错误。
    """
    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400

    f = request.files["file"]
    if not f.filename or not f.filename.endswith(".zip"):
        return jsonify({"error": "请上传 .zip 文件"}), 400

    ensure_dirs()
    processed_stems = get_processed_stems()
    existing_input = {Path(n).stem for n in get_input_files()}

    try:
        result = extract_zip_upload(
            f.stream,
            input_dir=Path(INPUT_DIR),
            processed_stems=processed_stems,
            existing_stems=existing_input,
        )
    except ZipUploadLimitError as exc:
        return jsonify({"error": f"解压失败: {exc}"}), 413
    except ZipUploadStorageError as exc:
        return jsonify({"error": f"上传暂存或写入失败: {exc}"}), 507
    except ZipUploadError as exc:
        return jsonify({"error": f"解压失败: {exc}"}), 400
    except OSError as exc:
        return jsonify({"error": f"上传暂存或写入失败: {exc}"}), 507

    if not result.new_files and not result.skipped_processed and not result.skipped_existing:
        return jsonify({"error": "未在 zip 中找到任何 .md 文件（已递归搜索嵌套 zip）"}), 400

    return jsonify({
        "new_files": result.new_files,
        "skipped_processed": result.skipped_processed,
        "skipped_existing": result.skipped_existing,
    })


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  路由：Pipeline 预览 / 运行 / SSE 流 / 停止 / 输出                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@admin_bp.route("/api/pipeline/settings")
@admin_required
def api_pipeline_settings():
    """返回 Pipeline 可调参数的当前值和允许范围。

    供前端 Settings 面板初始化时调用，显示当前的 temperature、
    verify_batch_genes、max_workers 值及其有效取值范围。

    返回:
        JSON: 包含 values（当前参数值字典）和 limits（参数范围字典）的 JSON 响应。
    """
    import nutrimaster.extraction.extract as _ext
    import nutrimaster.extraction.verify as _ver

    return jsonify({
        "values": {
            "temperature": getattr(_ext, "TEMPERATURE", PIPELINE_DEFAULTS["temperature"]),
            "verify_batch_genes": getattr(_ver, "GENES_PER_BATCH", PIPELINE_DEFAULTS["verify_batch_genes"]),
            "max_workers": PIPELINE_DEFAULTS["max_workers"],
        },
        "limits": PIPELINE_LIMITS,
    })


@admin_bp.route("/api/pipeline/preview", methods=["POST"])
@admin_required
def api_pipeline_preview():
    try:
        _pipeline_execution_gate.try_acquire()
    except ExperimentBusyError:
        return jsonify({"error": "Pipeline 正在运行中"}), 409
    try:
        return _api_pipeline_preview_exclusive()
    finally:
        _pipeline_execution_gate.release()


def _api_pipeline_preview_exclusive():
    """同步处理待处理队列中的第一篇论文并返回结果预览。

    执行真实的论文处理流程：.md 文件会被移入 processed 目录，
    生成的 verified JSON 写入 data/ 目录。用户可在确认格式正确后
    再运行批量处理。前端可通过 settings 字段覆盖 temperature 等参数。

    返回:
        JSON: 包含 filename（文件名）、status（处理状态）、verified_json（生成的 JSON 数据）、
              token_summary（token 使用概要）、token_report（token 报告路径）。
              若无待处理文件返回 400，若 Pipeline 正在运行返回 409。
    """
    files = get_input_files()
    if not files:
        return jsonify({"error": "input 目录中没有待处理文件"}), 400

    with _lock:
        if pipeline_state["running"]:
            return jsonify({"error": "Pipeline 正在运行中"}), 409

    # 读取并应用前端传入的参数
    body = request.get_json(silent=True) or {}
    if "settings" in body:
        _apply_settings(body["settings"])

    filename = files[0]
    md_path = Path(INPUT_DIR) / filename
    stem = Path(filename).stem
    tracker = TokenTracker(model=os.getenv("EXTRACTOR_MODEL", "unknown"))

    ensure_dirs()

    # 捕获 process_one_paper 的 print 输出
    import sys
    tee = _TeeWriter(sys.__stdout__)
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = tee
    try:
        result = process_one_paper(md_path, stem, tracker)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    pipeline_state["output"] = tee.get_tail(5000)
    token_report = save_token_report(tracker, "admin-preview")

    # 读取生成的 verified JSON（如果处理成功）
    verified_path = DATA_DIR / f"{stem}_nutri_plant_verified.json"
    verified_data = None
    if verified_path.exists():
        try:
            verified_data = json.loads(verified_path.read_text(encoding="utf-8"))
        except MemoryError:
            raise
        except Exception:
            pass

    index_build = None
    index_build_error = None
    if result.get("status") == "processed" and verified_data is not None:
        try:
            index_build = _refresh_index(DATA_DIR, force=False)
        except MemoryError:
            raise
        except Exception as exc:
            index_build_error = f"{type(exc).__name__}: {exc}"

    response = jsonify({
        "filename": filename,
        "status": result.get("status", "failed"),
        "verified_json": verified_data,
        "token_summary": tracker.get_summary(),
        "token_report": token_report,
        "index_build": index_build,
        "index_build_error": index_build_error,
    })
    return (response, 503) if index_build_error else response


@admin_bp.route("/api/pipeline/run", methods=["POST"])
@admin_required
def api_pipeline_run():
    try:
        _pipeline_execution_gate.try_acquire()
    except ExperimentBusyError:
        return jsonify({"error": "Pipeline 正在运行中"}), 409
    gate_lease = {
        "transferred": False,
        # Capture the exact acquired instance. App factories or tests may
        # reconfigure the module-level gate while this worker is alive.
        "gate": _pipeline_execution_gate,
    }
    try:
        return _api_pipeline_run_exclusive(gate_lease=gate_lease)
    finally:
        if not gate_lease["transferred"]:
            gate_lease["gate"].release()


def _api_pipeline_run_exclusive(*, gate_lease: dict[str, object] | None = None):
    """启动后台线程批量处理所有待处理的 Markdown 论文文件。

    使用 ThreadPoolExecutor 并行处理 input/ 目录中的所有 .md 文件。
    自动跳过 data/ 目录中已有 verified JSON 的论文。
    处理进度通过 SSE (/api/pipeline/stream) 实时推送。
    全部完成后自动重建 RAG 向量索引。

    前端可通过请求体的 settings 字段覆盖参数：
      - temperature: 提取 API 温度
      - verify_batch_genes: 每批验证基因数
      - max_workers: 并行 worker 数

    返回:
        JSON: 包含 message（启动消息）、total（待处理总数）、
              skipped（跳过已处理的数量）、workers（并行数）。
              若已在运行返回 409，若无待处理文件返回 400。
    """
    with _lock:
        if pipeline_state["running"]:
            return jsonify({"error": "Pipeline 正在运行中"}), 409

    files = get_input_files()
    if not files:
        return jsonify({"error": "没有待处理文件"}), 400

    # 读取前端传入的参数
    body = request.get_json(silent=True) or {}
    settings = body.get("settings", {})
    max_workers = int(settings.get("max_workers", PIPELINE_DEFAULTS["max_workers"]))
    max_workers = max(PIPELINE_LIMITS["max_workers"][0],
                      min(PIPELINE_LIMITS["max_workers"][1], max_workers))

    # 应用 temperature 和 verify_batch_genes
    if settings:
        _apply_settings(settings)

    # 初始化 pipeline 状态
    with _lock:
        pipeline_state["running"] = True
        pipeline_state["stop_requested"] = False
        pipeline_state["done"] = 0
        pipeline_state["current_file"] = ""
        # 清空旧事件
        while not pipeline_state["events"].empty():
            try:
                pipeline_state["events"].get_nowait()
            except queue.Empty:
                break

    # ── 过滤掉 data 中已有 verified JSON 的文件（只看 data 目录，不管 report）──
    processed_stems = get_processed_stems()
    todo_files = [f for f in files if Path(f).stem not in processed_stems]
    skipped = len(files) - len(todo_files)

    if not todo_files:
        with _lock:
            pipeline_state["running"] = False
        return jsonify({"error": f"全部 {len(files)} 篇已在 data 中，无需处理"}), 400

    with _lock:
        pipeline_state["total"] = len(todo_files)

    def run():
        """后台线程主函数：并行处理所有论文，完成后重建 RAG 索引。

        负责捕获控制台输出、追踪 token 用量、发送 SSE 事件、
        处理停止信号、以及在完成后触发索引重建。
        """
        import sys
        tee = _TeeWriter(sys.__stdout__)
        pipeline_state["_tee"] = tee
        sys.stdout = sys.stderr = tee

        stopped = False
        tracker = None

        try:
            eq = pipeline_state["events"]
            tracker = TokenTracker(model=os.getenv("EXTRACTOR_MODEL", "unknown"))
            pipeline_state["tracker"] = tracker
            ensure_dirs()

            if skipped > 0:
                print(f"  ⏭️  跳过 {skipped} 篇（data 中已有 verified JSON）")

            eq.put(("start", {"total": len(todo_files), "workers": max_workers, "skipped": skipped}))

            if max_workers > 1 and len(todo_files) > 1:
                # 并行模式下不追踪单一 current_file，前端只显示整体并行状态。
                eq.put(("processing", {
                    "index": 0,
                    "filename": f"{len(todo_files)} papers (parallel, {max_workers} workers)",
                    "total": len(todo_files),
                }))

            def _stop_requested() -> bool:
                """检查用户是否请求停止 Pipeline。

                返回:
                    bool: 若用户已发送停止信号则返回 True。
                """
                with _lock:
                    return pipeline_state["stop_requested"]

            def _on_paper_start(filename: str, index: int, total: int, parallel: bool):
                """单篇论文开始处理时的回调函数。

                顺序模式下逐篇广播当前正在处理的文件名；并行模式下跳过（已在启动时发送整体状态）。

                参数:
                    filename: 当前开始处理的文件名。
                    index: 当前文件在队列中的索引（从 0 开始）。
                    total: 待处理文件总数。
                    parallel: 是否为并行处理模式。
                """
                if parallel:
                    return
                with _lock:
                    pipeline_state["current_file"] = filename
                eq.put(("processing", {"index": index, "filename": filename, "total": total}))

            def _on_paper_done(filename: str, result: dict, done: int, total: int, parallel: bool):
                """单篇论文处理完成时的回调函数。

                更新 pipeline 进度状态并通过 SSE 推送 paper_done 事件。
                索引统一在批次结束后刷新，避免每篇论文重复加载整个语料。

                参数:
                    filename: 已完成处理的文件名。
                    result: 处理结果字典，包含 status 等字段。
                    done: 已完成处理的文件总数。
                    total: 待处理文件总数。
                    parallel: 是否为并行处理模式。
                """
                status = result.get("status", "failed")
                with _lock:
                    pipeline_state["done"] = done
                eq.put(("paper_done", {
                    "index": done - 1,
                    "filename": filename,
                    "status": status,
                    "done": done,
                    "total": total,
                }))

            run_result = run_pipeline_batch(
                todo_files,
                input_dir=Path(INPUT_DIR),
                workers=max_workers,
                tracker=tracker,
                stop_requested=_stop_requested,
                on_paper_start=_on_paper_start,
                on_paper_done=_on_paper_done,
                # The Admin UI only needs per-paper status and aggregate
                # progress. Retaining every full verification report in the
                # online Web process would grow for the entire corpus.
                retain_reports=False,
            )
            stopped = run_result["stopped"]
            # ── Pipeline 结束：将索引任务交给独立受限 builder ─────────────
            eq.put(("queueing_index", {}))
            try:
                build_job = _refresh_index(DATA_DIR, force=False)
                eq.put(("index_queued", {"job_id": build_job["job_id"]}))
            except MemoryError:
                raise
            except Exception as e:
                eq.put(("index_error", {"error": str(e)}))

            # ── 发送完成/停止事件 ──────────────────────────────────────
            token_summary = tracker.get_summary()
            token_report = save_token_report(tracker, "admin-run")

            if not stopped:
                eq.put(("complete", {
                    "done": len(todo_files),
                    "total": len(todo_files),
                    "token_summary": token_summary,
                    "token_report": token_report,
                }))
            else:
                eq.put(("stopped", {
                    "done": pipeline_state["done"],
                    "total": len(todo_files),
                    "token_summary": token_summary,
                    "token_report": token_report,
                }))

        finally:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            pipeline_state["output"] = tee.get_tail(5000)
            pipeline_state["_tee"] = None
            pipeline_state["tracker"] = None

            with _lock:
                pipeline_state["running"] = False
                pipeline_state["current_file"] = ""
            if gate_lease is not None:
                gate_lease["gate"].release()

    t = threading.Thread(target=run, daemon=True)
    pipeline_state["thread"] = t
    if gate_lease is not None:
        # Mark ownership before start: a very short worker may finish before
        # this WSGI thread resumes, but it still releases exactly once.
        gate_lease["transferred"] = True
    try:
        t.start()
    except BaseException:
        if gate_lease is not None:
            gate_lease["transferred"] = False
        with _lock:
            pipeline_state["running"] = False
            pipeline_state["thread"] = None
        raise

    msg = f"Pipeline 已启动，共 {len(todo_files)} 篇论文，{max_workers} 并行"
    if skipped > 0:
        msg += f"（跳过 {skipped} 篇已处理）"

    return jsonify({
        "message": msg,
        "total": len(todo_files),
        "skipped": skipped,
        "workers": max_workers,
    })


@admin_bp.route("/api/pipeline/stream")
@admin_required
def api_pipeline_stream():
    """SSE 端点：流式推送 Pipeline 处理进度事件。

    支持的事件类型:
      - connected: 连接成功
      - start: Pipeline 开始运行
      - processing: 开始处理某篇论文
      - paper_done: 某篇论文处理完成
      - queueing_index: 正在持久化索引构建任务
      - index_queued: 独立 builder 已收到构建任务（尚未生效）
      - index_error: 索引重建失败
      - complete: 全部处理完成
      - stopped: 被用户停止
      - heartbeat: 30 秒无事件时的心跳保活
      - idle: Pipeline 已不在运行（兜底状态）

    返回:
        Response: SSE 流式响应（text/event-stream），持续推送事件直到 Pipeline 完成或停止。
    """
    def generate():
        """SSE 事件生成器。

        从事件队列中持续读取事件并格式化为 SSE 字符串。
        30 秒无新事件时发送心跳保活；Pipeline 不再运行时发送 idle 事件并结束。

        生成:
            str: 格式化的 SSE 事件字符串。
        """
        eq = pipeline_state["events"]
        yield _sse_event("connected", {"message": "SSE connected"})
        while True:
            try:
                event_type, data = eq.get(timeout=30)
                yield _sse_event(event_type, data)
                if event_type in ("complete", "stopped"):
                    break
            except queue.Empty:
                # 30秒没有新事件 → 发心跳保活
                yield _sse_event("heartbeat", {})
                with _lock:
                    if not pipeline_state["running"]:
                        yield _sse_event("idle", {})
                        break

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@admin_bp.route("/api/pipeline/stop", methods=["POST"])
@admin_required
def api_pipeline_stop():
    """发送停止 Pipeline 的信号。

    发送停止信号后，当前正在处理的论文会完成处理后停止，不会清理半成品文件。
    若 Pipeline 未在运行，返回 400 错误。

    返回:
        JSON: 包含停止确认消息的 JSON 响应。
    """
    with _lock:
        if not pipeline_state["running"]:
            return jsonify({"error": "Pipeline 未在运行"}), 400
        pipeline_state["stop_requested"] = True

    return jsonify({"message": "停止信号已发送，当前论文处理完成后停止"})


@admin_bp.route("/api/pipeline/output")
@admin_required
def api_pipeline_output():
    """返回 Pipeline 的控制台输出内容（最后 5000 字符）。

    运行中时从 _TeeWriter 实例实时读取；运行结束后从缓存的 output 字符串读取。

    返回:
        JSON: 包含 output（控制台输出文本）的 JSON 响应。
    """
    tee = pipeline_state.get("_tee")
    if tee:
        return jsonify({"output": tee.get_tail(5000)})
    return jsonify({"output": pipeline_state.get("output", "")})


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  路由：已处理论文列表                                                      ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@admin_bp.route("/api/papers")
@admin_required
def api_papers():
    """获取所有已处理论文的列表信息。

    扫描语料目录中的 verified JSON 文件，提取文件名、论文标题和修改时间，
    按修改时间倒序排列返回。

    返回:
        JSON: 论文信息列表，每项包含 filename（文件名）、title（论文标题）、modified（修改时间 ISO 格式）。
    """
    papers = []
    for f in sorted(DATA_DIR.glob("*_nutri_plant_verified.json")):
        if f.name == ".gitkeep":
            continue
        title = ""
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            title = data.get("Title", "")
        except MemoryError:
            raise
        except Exception:
            pass
        papers.append({
            "filename": f.name,
            "title": title,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
        })
    papers.sort(key=lambda p: p["modified"], reverse=True)
    return jsonify(papers)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  路由：Prompt / Schema 编辑器                                             ║
# ║  保存后自动清除 lru_cache，下次 pipeline 使用新版本                          ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@admin_bp.route("/api/prompt", methods=["GET"])
@admin_required
def api_prompt_get():
    """读取当前 Prompt 模板文件的内容。

    返回:
        JSON: 包含 content（文件内容）和 path（文件路径）的 JSON 响应。
              若文件不存在返回 404 错误。
    """
    if not PROMPT_PATH.exists():
        return jsonify({"error": "Prompt 文件不存在"}), 404
    return jsonify({"content": PROMPT_PATH.read_text(encoding="utf-8"), "path": str(PROMPT_PATH)})


@admin_bp.route("/api/prompt", methods=["PUT"])
@admin_required
def api_prompt_put():
    """保存 Prompt 模板文件内容。

    将请求体中的 content 字段写入 Prompt 文件，并清除 extractor 模块的
    lru_cache 缓存，使新内容在下次 Pipeline 运行时生效。

    返回:
        JSON: 包含保存确认消息和内容长度的 JSON 响应。
              若请求体缺少 content 字段返回 400 错误。
    """
    body = request.get_json(silent=True)
    if not body or "content" not in body:
        return jsonify({"error": "缺少 content 字段"}), 400
    PROMPT_PATH.write_text(body["content"], encoding="utf-8")
    # 清除 lru_cache → 下次 extract 会重新读取文件
    try:
        from nutrimaster.extraction.extract import _load_prompt
        _load_prompt.cache_clear()
    except MemoryError:
        raise
    except Exception:
        pass
    return jsonify({"message": "Prompt 已保存", "length": len(body["content"])})


@admin_bp.route("/api/schema", methods=["GET"])
@admin_required
def api_schema_get():
    """读取当前 JSON Schema 定义文件的内容。

    返回:
        JSON: 包含 content（文件内容）和 path（文件路径）的 JSON 响应。
              若文件不存在返回 404 错误。
    """
    if not SCHEMA_PATH.exists():
        return jsonify({"error": "Schema 文件不存在"}), 404
    return jsonify({"content": SCHEMA_PATH.read_text(encoding="utf-8"), "path": str(SCHEMA_PATH)})


@admin_bp.route("/api/schema", methods=["PUT"])
@admin_required
def api_schema_put():
    """保存 JSON Schema 定义文件内容。

    先验证提交内容是否为合法 JSON，再写入文件，
    并清除 extractor 模块的 lru_cache 缓存使新 Schema 在下次运行时生效。

    返回:
        JSON: 包含保存确认消息和内容长度的 JSON 响应。
              若缺少 content 字段返回 400，若 JSON 格式无效返回 400。
    """
    body = request.get_json(silent=True)
    if not body or "content" not in body:
        return jsonify({"error": "缺少 content 字段"}), 400
    # 验证 JSON 格式
    try:
        json.loads(body["content"])
    except json.JSONDecodeError as e:
        return jsonify({"error": f"无效的 JSON: {e}"}), 400
    SCHEMA_PATH.write_text(body["content"], encoding="utf-8")
    try:
        from nutrimaster.extraction.extract import _load_extract_all_schema
        _load_extract_all_schema.cache_clear()
    except MemoryError:
        raise
    except Exception:
        pass
    return jsonify({"message": "Schema 已保存", "length": len(body["content"])})


@admin_bp.route("/api/pipeline/tokens")
@admin_required
def api_pipeline_tokens():
    """获取当前 Pipeline 运行的实时 Token 用量统计。

    若 Pipeline 未在运行或 tracker 不存在，返回 summary 为 null。

    返回:
        JSON: 包含 summary（token 用量概要字典或 null）的 JSON 响应。
    """
    tracker = pipeline_state.get("tracker")
    if tracker is None:
        return jsonify({"summary": None})
    return jsonify({"summary": tracker.get_summary()})


@admin_bp.route("/api/index/status")
@admin_required
def api_index_status():
    """查询 RAG 索引状态。

    返回索引的当前状态，包括已索引文件数、缺失文件数、总 chunks 数等。

    返回:
        JSON: 包含索引状态信息的 JSON 响应。
        {
            "total_files": int,      # corpus 中的总文件数
            "indexed_files": int,    # 已索引的文件数
            "missing_files": int,    # 未索引的文件数
            "total_chunks": int,     # 总 chunk 数
            "embedding_shape": [...], # embedding 维度
            "last_updated": str,     # 最后更新时间（ISO 格式）
            "is_synced": bool        # 是否完全同步
        }
    """
    from datetime import datetime

    corpus_dir = Path(SETTINGS.rag.data_dir)

    # 统计 corpus 文件
    all_files = list(corpus_dir.glob("*.json"))
    total_files = len(all_files)

    # Reuse the one retriever owned by FastAPI. Constructing a diagnostic
    # retriever here used to deserialize another corpus-sized chunks.pkl.
    if _index_status_handler is not None:
        status = _index_status_handler()
        generation_id = status.get("generation_id")
        total_chunks = int(status.get("chunks_loaded", 0))
        embedding_shape = status.get("embedding_shape")
        indexed_files = int(status.get("manifest_files") or 0)
        generation_manifest = Path(str(status.get("index_dir", ""))) / "manifest.json"
        if generation_manifest.is_file():
            last_updated = datetime.fromtimestamp(
                generation_manifest.stat().st_mtime
            ).isoformat()
        else:
            last_updated = None
    else:
        total_chunks = 0
        embedding_shape = None
        indexed_files = 0
        last_updated = None
        generation_id = None
    missing_files = max(0, total_files - indexed_files)

    build_status = (
        _index_build_status_handler()
        if _index_build_status_handler is not None
        else None
    )

    return jsonify({
        "total_files": total_files,
        "indexed_files": indexed_files,
        "missing_files": missing_files,
        "total_chunks": total_chunks,
        "embedding_shape": embedding_shape,
        "last_updated": last_updated,
        "is_synced": missing_files == 0,
        "generation_id": generation_id,
        "build": build_status,
    })


@admin_bp.route("/api/index/rebuild", methods=["POST"])
@admin_required
def api_index_rebuild():
    """手动触发 RAG 索引重建。

    支持增量或完全重建模式。构建始终由独立受限 builder 异步执行；
    Web 进程不支持同步构建。

    请求体:
        {
            "force": false,  # 是否强制完全重建（默认 false，增量模式）
            "async": true    # 是否异步执行（默认 true）
        }

    返回:
        JSON: 包含操作状态的 JSON 响应。
    """
    body = request.get_json(silent=True) or {}
    force = body.get("force", False)
    async_mode = body.get("async", True)
    if not isinstance(force, bool) or not isinstance(async_mode, bool):
        return jsonify({"error": "force 和 async 必须为布尔值"}), 400
    if not async_mode:
        return jsonify({
            "error": "同步重建已禁用；索引只能由独立 builder 异步构建"
        }), 400
    try:
        job = _refresh_index(DATA_DIR, force=force)
    except MemoryError:
        raise
    except Exception as exc:
        return jsonify({"error": f"索引构建任务提交失败: {exc}"}), 503
    return jsonify({
        "message": "索引构建任务已持久化排队；尚未构建或生效",
        "status": "queued",
        "job_id": job["job_id"],
        "force": force,
        "async": True,
    }), 202
