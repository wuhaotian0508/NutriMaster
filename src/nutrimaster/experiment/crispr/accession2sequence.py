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


def _read_accession_file(accession_file: Path) -> list[tuple[str, str, str]]:
    """读取单个 accession 文件，返回 (gene, species, accession) 三元组列表。"""
    rows = []
    with accession_file.open(encoding="utf-8") as file:
        for line in file:
            parts = line.strip().split("\t")
            if len(parts) >= 3 and parts[2]:
                rows.append((parts[0], parts[1], parts[2]))
    return rows


def run_accession2sequence(accession_files: list[Path], work_dir: Path) -> list[Path]:
    """读取各物种 accession 文件，从 NCBI 下载对应的 FASTA 序列，按物种分别保存。

    每个输入文件对应一个物种，TSV 格式（基因名\\t物种\\taccession）。
    每个物种的序列写入 work_dir/{species}_sequence.fas，序列 header 为 >{species} {accession}。

    Args:
        accession_files: gene2accession 生成的各物种 accession 文件路径列表。
        work_dir: 工作目录，FASTA 输出文件将写入此目录。

    Returns:
        list[Path]: 生成的各物种 FASTA 文件路径列表。

    Raises:
        ValueError: 所有文件中均无有效 accession，或所有下载均失败时抛出。
    """
    species_rows: dict[str, list[tuple[str, str, str]]] = {}
    for accession_file in accession_files:
        for gene, species, accession in _read_accession_file(accession_file):
            species_rows.setdefault(species, []).append((gene, species, accession))

    if not species_rows:
        raise ValueError("没有有效的 accession 可供下载序列")

    output_files = []
    for species, rows in species_rows.items():
        filename = species.replace(" ", "_") + "_sequence.fas"
        fasta_file = work_dir / filename
        with fasta_file.open("w", encoding="utf-8") as output:
            for gene, sp, accession in rows:
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
                lines[0] = f">{sp.replace(' ', '_')}_{gene}_{accession}"
                output.write("\n".join(lines) + "\n")
                time.sleep(0.34)
        if fasta_file.stat().st_size == 0:
            logger.warning("物种 %s 未能下载到任何基因序列，跳过", species)
            fasta_file.unlink()
            continue
        output_files.append(fasta_file)

    if not output_files:
        raise ValueError("未能下载到任何基因序列")
    return output_files


__all__ = ["run_accession2sequence"]
