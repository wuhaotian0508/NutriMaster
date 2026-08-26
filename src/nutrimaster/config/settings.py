from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


REQUIRED_REAL_SERVICE_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "MAIN_MODEL",
    "JINA_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
)


def _find_dotenv(start: Path) -> Path | None:
    """从指定路径开始向上查找 .env 文件。

    从起始路径逐级向上遍历目录树，查找第一个存在的 .env 文件。

    Args:
        start: 起始查找路径。如果是文件则从其所在目录开始查找。

    Returns:
        找到的 .env 文件路径，未找到则返回 None。
    """
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate_dir in (current, *current.parents):
        candidate = candidate_dir / ".env"
        if candidate.exists():
            return candidate
    return None


def _default_project_root() -> Path:
    """获取默认的项目根目录路径。

    通过当前文件位置向上回溯三级目录来推断项目根目录。

    Returns:
        项目根目录的 Path 对象。
    """
    return Path(__file__).resolve().parents[3]


def _load_env_with_dotenv(start: Path | None = None) -> dict[str, str]:
    """加载环境变量，合并 .env 文件和系统环境变量。

    先从 .env 文件加载变量，再用系统环境变量覆盖同名键，
    确保系统环境变量优先级更高。

    Args:
        start: 查找 .env 文件的起始路径。为 None 时使用当前工作目录。

    Returns:
        合并后的环境变量字典。
    """
    values: dict[str, str] = {}
    dotenv_path = _find_dotenv(start or Path.cwd())
    if dotenv_path is not None:
        for key, value in dotenv_values(dotenv_path).items():
            if value is not None:
                values[key] = value
    values.update(os.environ)
    return values


def _as_int(source: Mapping[str, str], key: str, default: int) -> int:
    """从字符串映射中获取整数值。

    Args:
        source: 字符串键值对映射（如环境变量字典）。
        key: 要查找的键名。
        default: 键不存在或值为空时返回的默认值。

    Returns:
        转换后的整数值，或默认值。
    """
    raw = source.get(key)
    if raw in (None, ""):
        return default
    return int(raw)


def _as_bool(source: Mapping[str, str], key: str, default: bool = False) -> bool:
    """从字符串映射中获取布尔值。

    支持的真值字符串（不区分大小写）："true"、"1"、"yes"、"on"。

    Args:
        source: 字符串键值对映射（如环境变量字典）。
        key: 要查找的键名。
        default: 键不存在或值为空时返回的默认值。

    Returns:
        转换后的布尔值，或默认值。
    """
    raw = source.get(key)
    if raw in (None, ""):
        return default
    return raw.lower() in {"true", "1", "yes", "on"}


@dataclass(frozen=True)
class RagSettings:
    """RAG（检索增强生成）运行时配置。

    包含索引、检索、个人文献库和 Web 服务等相关设置项。

    Attributes:
        data_dir: 语料数据目录。
        index_dir: 索引文件存储目录。
        personal_lib_dir: 用户个人文献库目录。
        jina_embedding_url: Jina 嵌入向量 API 地址。
        jina_rerank_url: Jina 重排序 API 地址。
        embedding_model: 嵌入向量模型名称。
        rerank_model: 重排序模型名称。
        top_k_retrieval: 初步检索返回的文档数量。
        top_k_rerank: 重排序后保留的文档数量。
        deep_top_k_retrieval: 深度检索返回的文档数量。
        deep_top_k_rerank: 深度检索重排序后保留的文档数量。
        chunk_size: 文本分块大小（字符数）。
        chunk_overlap: 文本分块重叠区域大小（字符数）。
        pubmed_max_results: PubMed 搜索最大返回结果数。
        max_pdf_size_mb: 允许上传的 PDF 文件最大大小（MB）。
        max_files_per_user: 每个用户最大文件数。
        web_host: Web 服务监听地址。
        web_port: Web 服务端口。
        admin_port: 管理后台端口。
        debug: 是否启用调试模式。
    """

    data_dir: Path
    index_dir: Path
    personal_lib_dir: Path
    jina_embedding_url: str = "https://api.jina.ai/v1/embeddings"
    jina_rerank_url: str = "https://api.jina.ai/v1/rerank"
    embedding_model: str = "jina-embeddings-v3"
    rerank_model: str = "jina-reranker-v2-base-multilingual"
    top_k_retrieval: int = 20
    top_k_rerank: int = 10
    deep_top_k_retrieval: int = 20
    deep_top_k_rerank: int = 10
    chunk_size: int = 1500
    chunk_overlap: int = 200
    pubmed_max_results: int = 10
    max_pdf_size_mb: int = 50
    max_files_per_user: int = 20
    web_host: str = "0.0.0.0"
    web_port: int = 5000
    admin_port: int = 5501
    debug: bool = False

    @classmethod
    def from_env(cls, project_root: Path, env: Mapping[str, str]) -> "RagSettings":
        """从环境变量创建 RagSettings 实例。

        读取环境变量中的配置值，未设置的项使用基于项目根目录的默认值。

        Args:
            project_root: 项目根目录路径，用于推算默认数据目录。
            env: 环境变量映射字典。

        Returns:
            根据环境变量配置的 RagSettings 实例。
        """
        data_root = project_root / "data"
        corpus_root = data_root / "corpus"
        return cls(
            data_dir=Path(env.get("RAG_DATA_DIR", corpus_root)),
            index_dir=Path(env.get("RAG_INDEX_DIR", data_root / "index")),
            personal_lib_dir=Path(env.get("RAG_PERSONAL_LIB_DIR", data_root / "personal_lib")),
            top_k_retrieval=_as_int(env, "TOP_K_RETRIEVAL", 20),
            top_k_rerank=_as_int(env, "TOP_K_RERANK", 10),
            deep_top_k_retrieval=_as_int(env, "DEEP_TOP_K_RETRIEVAL", 20),
            deep_top_k_rerank=_as_int(env, "DEEP_TOP_K_RERANK", 10),
            chunk_size=_as_int(env, "CHUNK_SIZE", 1500),
            chunk_overlap=_as_int(env, "CHUNK_OVERLAP", 200),
            pubmed_max_results=_as_int(env, "PUBMED_MAX_RESULTS", 10),
            max_pdf_size_mb=_as_int(env, "MAX_PDF_SIZE_MB", 50),
            max_files_per_user=_as_int(env, "MAX_FILES_PER_USER", 20),
            web_host=env.get("WEB_HOST", "0.0.0.0"),
            web_port=_as_int(env, "WEB_PORT", 5000),
            admin_port=_as_int(env, "ADMIN_PORT", 5501),
            debug=_as_bool(env, "DEBUG", False),
        )


@dataclass(frozen=True)
class LlmSettings:
    """LLM 服务连接配置。

    Attributes:
        api_key: API 密钥。
        base_url: API 基础 URL。
        model: 使用的模型名称。
        experiment_model: 实验预览使用的模型名称，默认沿用主模型。
    """
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    experiment_model: str = ""


@dataclass(frozen=True)
class AuthSettings:
    """认证服务（Supabase）相关配置。

    Attributes:
        supabase_url: Supabase 项目 URL。
        supabase_service_role_key: Supabase 服务角色密钥（拥有管理员权限）。
        supabase_anon_key: Supabase 匿名密钥（用于客户端访问）。
        site_url: 站点 URL，用于邮件链接等回调地址。
    """
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""
    site_url: str = ""


@dataclass(frozen=True)
class EmailSettings:
    """邮件发送服务配置。

    Attributes:
        smtp_user: SMTP 登录用户名（发件人邮箱地址）。
        smtp_password: SMTP 登录密码或授权码。
        smtp_host: SMTP 服务器地址。
        smtp_port: SMTP 服务器端口。
        smtp_from_name: 发件人显示名称。
    """
    smtp_user: str = "nutrimaster@163.com"
    smtp_password: str = ""
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_from_name: str = "NutriMaster"


@dataclass(frozen=True)
class Settings:
    """NutriMaster 全局运行时配置。

    汇聚所有子服务配置（LLM、认证、邮件、RAG），
    提供统一的配置加载入口。

    Attributes:
        project_root: 项目根目录路径。
        openai_api_key: OpenAI 兼容 API 密钥。
        openai_base_url: OpenAI 兼容 API 基础 URL。
        model: 主要使用的 LLM 模型名称。
        experiment_model: CRISPR/转基因实验提取使用的 LLM 模型名称。
        jina_api_key: Jina API 密钥（用于嵌入和重排序）。
        supabase_url: Supabase 项目 URL。
        supabase_service_role_key: Supabase 服务角色密钥。
        supabase_anon_key: Supabase 匿名密钥。
        site_url: 站点 URL。
        smtp_user: SMTP 用户名。
        smtp_password: SMTP 密码。
        smtp_host: SMTP 服务器地址。
        smtp_port: SMTP 端口。
        smtp_from_name: 发件人显示名称。
        llm: LLM 子配置。
        auth: 认证子配置。
        email: 邮件子配置。
        rag: RAG 子配置（可为 None）。
    """

    project_root: Path
    openai_api_key: str = ""
    openai_base_url: str = ""
    model: str = ""
    experiment_model: str = ""
    jina_api_key: str = ""
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""
    site_url: str = ""
    smtp_user: str = "nutrimaster@163.com"
    smtp_password: str = ""
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_from_name: str = "NutriMaster"
    llm: LlmSettings = field(default_factory=LlmSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    email: EmailSettings = field(default_factory=EmailSettings)
    rag: RagSettings | None = None

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        project_root: Path | None = None,
    ) -> "Settings":
        """从环境变量创建 Settings 实例。

        优先使用传入的 env 映射，否则自动加载 .env 文件和系统环境变量。
        所有子配置（LLM、认证、邮件、RAG）均在此统一初始化。

        Args:
            env: 可选的环境变量映射。为 None 时自动从 .env 文件和系统环境变量加载。
            project_root: 可选的项目根目录。为 None 时使用环境变量 NUTRIMASTER_ROOT
                或自动推断的默认路径。

        Returns:
            根据环境变量完整初始化的 Settings 实例。
        """
        source = _load_env_with_dotenv(project_root) if env is None else env
        default_root = project_root or _default_project_root()
        project_root = Path(source.get("NUTRIMASTER_ROOT", default_root))
        llm = LlmSettings(
            api_key=source.get("OPENAI_API_KEY", ""),
            base_url=source.get("OPENAI_BASE_URL", ""),
            model=source.get("MAIN_MODEL", ""),
            experiment_model=(
                source.get("EXPERIMENT_MODEL", "")
                or source.get("MAIN_MODEL", "")
            ),
        )
        auth = AuthSettings(
            supabase_url=source.get("SUPABASE_URL", ""),
            supabase_service_role_key=source.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            supabase_anon_key=source.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", ""),
            site_url=source.get("SITE_URL", ""),
        )
        email = EmailSettings(
            smtp_user=source.get("SMTP_USER", "nutrimaster@163.com"),
            smtp_password=source.get("SMTP_PASSWORD", ""),
            smtp_host=source.get("SMTP_HOST", "smtp.163.com"),
            smtp_port=_as_int(source, "SMTP_PORT", 465),
            smtp_from_name=source.get("SMTP_FROM_NAME", "NutriMaster"),
        )
        return cls(
            project_root=project_root,
            openai_api_key=llm.api_key,
            openai_base_url=llm.base_url,
            model=llm.model,
            experiment_model=llm.experiment_model,
            jina_api_key=source.get("JINA_API_KEY", ""),
            supabase_url=auth.supabase_url,
            supabase_service_role_key=auth.supabase_service_role_key,
            supabase_anon_key=auth.supabase_anon_key,
            site_url=auth.site_url,
            smtp_user=email.smtp_user,
            smtp_password=email.smtp_password,
            smtp_host=email.smtp_host,
            smtp_port=email.smtp_port,
            smtp_from_name=email.smtp_from_name,
            llm=llm,
            auth=auth,
            email=email,
            rag=RagSettings.from_env(project_root, source),
        )

    def missing_real_service_keys(self) -> list[str]:
        """检查并返回缺失的必要服务配置键列表。

        对比 REQUIRED_REAL_SERVICE_KEYS 中定义的必填键，
        返回当前配置中值为空的键名列表。

        Returns:
            缺失的配置键名列表。如果所有必要配置都已设置，返回空列表。
        """
        values = {
            "OPENAI_API_KEY": self.openai_api_key,
            "OPENAI_BASE_URL": self.openai_base_url,
            "MAIN_MODEL": self.model,
            "JINA_API_KEY": self.jina_api_key,
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key,
        }
        return [key for key in REQUIRED_REAL_SERVICE_KEYS if not values[key]]
