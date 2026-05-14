from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_ENTREZ_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_ENTREZ_EMAIL = "nutrimaster_rag@example.com"
_MAX_RETRIES = 3


def _has_env_proxy() -> bool:
    """检查当前环境中是否设置了代理相关的环境变量。

    Returns:
        bool: 如果存在任意一个代理环境变量（http_proxy、https_proxy、all_proxy 及其大写形式），返回 True；否则返回 False。
    """
    return any(
        os.getenv(name)
        for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
    )


def _build_session(use_env_proxy: bool) -> requests.Session:
    """构建一个配置好请求头的 HTTP 会话对象。

    Args:
        use_env_proxy: 是否使用系统环境变量中的代理设置。True 表示信任环境代理，False 表示直连。

    Returns:
        requests.Session: 配置好 User-Agent、Accept-Encoding 和 Connection 头的会话对象。
    """
    session = requests.Session()
    session.trust_env = use_env_proxy
    session.headers.update(
        {
            "User-Agent": "nutrimaster_rag/1.0",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
    )
    return session


def _fetch_fasta_text(accession: str, params: dict) -> str:
    """从 NCBI Entrez efetch 接口下载指定 accession 的 FASTA 序列文本。

    会先尝试通过环境代理连接，若失败则回退到直连。每种模式下最多重试 _MAX_RETRIES 次。

    Args:
        accession: NCBI 核酸数据库的 accession 编号（如 NM_001234）。
        params: 传递给 efetch API 的查询参数字典。

    Returns:
        str: 去除首尾空白的 FASTA 格式文本。

    Raises:
        requests.exceptions.RequestException: 所有重试均失败时，抛出最后一次的网络异常。
        ValueError: 未能下载到序列时抛出。
    """
    last_error = None
    for use_env_proxy in ([True, False] if _has_env_proxy() else [True]):
        mode = "env-proxy" if use_env_proxy else "direct"
        session = _build_session(use_env_proxy)
        try:
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    response = session.get(_ENTREZ_EFETCH_URL, params=params, timeout=(10, 30))
                    response.raise_for_status()
                    return response.text.strip()
                except requests.exceptions.RequestException as exc:
                    last_error = exc
                    logger.warning(
                        "accession %s download failed (%s attempt %d/%d): %s",
                        accession,
                        mode,
                        attempt,
                        _MAX_RETRIES,
                        exc,
                    )
                    if attempt < _MAX_RETRIES:
                        time.sleep(attempt)
        finally:
            session.close()
    if last_error is not None:
        raise last_error
    raise ValueError(f"未能下载 accession {accession} 的序列")


def run_accession2sequence(accession_file: Path, work_dir: Path) -> Path:
    """读取 accession 列表文件，从 NCBI 下载对应的 FASTA 序列并保存到工作目录。

    accession 文件格式为 TSV，每行至少三列，第三列为 accession 编号。
    下载的序列将合并写入 work_dir/sequence.fas 文件中，每条序列的 header 统一替换为 accession 编号。

    Args:
        accession_file: accession 列表文件路径，TSV 格式（基因名\\t物种\\taccession）。
        work_dir: 工作目录，FASTA 输出文件将写入此目录。

    Returns:
        Path: 生成的 FASTA 文件路径（work_dir/sequence.fas）。

    Raises:
        ValueError: 没有有效的 accession 可下载，或所有下载均失败导致输出文件为空。
    """
    fasta_file = work_dir / "sequence.fas"
    accessions = []
    with accession_file.open(encoding="utf-8") as file:
        for line in file:
            parts = line.strip().split("\t")
            if len(parts) >= 3 and parts[2]:
                accessions.append(parts[2])
    if not accessions:
        raise ValueError("没有有效的 accession 可供下载序列")
    with fasta_file.open("w", encoding="utf-8") as output:
        for accession in accessions:
            params = {
                "db": "nuccore",
                "id": accession,
                "rettype": "fasta",
                "retmode": "text",
                "email": _ENTREZ_EMAIL,
                "tool": "nutrimaster_rag",
            }
            try:
                text = _fetch_fasta_text(accession, params)
            except requests.exceptions.RequestException as exc:
                logger.warning("accession %s download failed, skipping: %s", accession, exc)
                continue
            if not text.startswith(">"):
                logger.warning("accession %s returned non-FASTA content, skipping", accession)
                continue
            lines = text.splitlines()
            lines[0] = f">{accession}"
            output.write("\n".join(lines) + "\n")
            time.sleep(0.34)
    if fasta_file.stat().st_size == 0:
        raise ValueError("未能下载到任何基因序列")
    return fasta_file


__all__ = ["run_accession2sequence"]
