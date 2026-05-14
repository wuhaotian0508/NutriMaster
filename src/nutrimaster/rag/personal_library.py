from __future__ import annotations

import json
import pickle
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import requests

from nutrimaster.config.settings import RagSettings, Settings

_SAFE_FILENAME_RE = re.compile(r"[^\w\u4e00-\u9fff\-. ]", re.UNICODE)


def sanitize_filename(name: str) -> str:
    """清洗文件名，移除不安全字符并规范化格式。

    将文件名中的特殊字符替换为下划线，合并连续的下划线或空格，
    去除首尾的下划线、点号和空格。若清洗后为空字符串，则生成
    基于时间戳的默认文件名。

    Args:
        name: 原始文件名，可包含路径前缀（会被自动去除）。

    Returns:
        清洗后的安全文件名字符串。仅保留字母、数字、中文、连字符、
        点号和空格。若结果为空则返回 ``upload_<timestamp>.pdf``。
    """
    name = Path(name).name
    name = _SAFE_FILENAME_RE.sub("_", name)
    name = re.sub(r"[_ ]{2,}", "_", name).strip("_. ")
    return name or f"upload_{int(time.time())}.pdf"


class PersonalLibrary:
    """个人文库：为每个用户提供独立的 PDF 文件管理和向量检索功能。

    支持 PDF 文件的上传、文本提取、分块、嵌入向量计算和相似度搜索。
    每个用户的数据（PDF 文件、索引、清单）存储在独立的目录中。

    Attributes:
        user_id: 用户唯一标识。
        rag_settings: RAG 配置。
        base_dir: 用户文库根目录。
        pdf_dir: PDF 文件存储目录。
        index_dir: 索引文件存储目录。
        chunks: 已加载的文本分块列表。
        embeddings: 已加载的嵌入向量矩阵。
        manifest: 文件清单字典。
    """

    def __init__(
        self,
        user_id: str,
        *,
        rag_settings: RagSettings | None = None,
        embed_texts=None,
    ):
        """初始化个人文库实例。

        为指定用户创建或加载个人文库，包括 PDF 存储目录、向量索引目录
        以及文件清单。初始化时会自动从磁盘加载已有的索引数据。

        Args:
            user_id: 用户唯一标识符，用于隔离不同用户的文库数据。
            rag_settings: RAG 配置对象。若为 None，则从环境变量自动加载。
            embed_texts: 自定义文本嵌入函数，签名为
                ``(texts: list[str]) -> np.ndarray``。若为 None，则使用
                基于 Jina API 的默认嵌入方法。

        Raises:
            RuntimeError: 当 RAG 配置初始化失败时抛出。
        """
        self.user_id = user_id
        self.rag_settings = rag_settings or Settings.from_env().rag
        if self.rag_settings is None:
            raise RuntimeError("RAG settings failed to initialize")
        self._embed_texts = embed_texts or self._default_embed_texts
        self.base_dir = self.rag_settings.personal_lib_dir / user_id
        self.pdf_dir = self.base_dir / "pdfs"
        self.index_dir = self.base_dir / "index"
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.chunks: list[dict[str, Any]] = []
        self.embeddings: np.ndarray | None = None
        self.manifest: dict[str, Any] = {}
        self._load_index()

    def _load_index(self):
        """从磁盘加载索引数据。

        尝试从索引目录中读取分块数据（chunks.pkl）、嵌入向量（embeddings.npy）
        和文件清单（manifest.json）。若文件不存在则保持初始空状态。
        """
        chunks_file = self.index_dir / "chunks.pkl"
        embeddings_file = self.index_dir / "embeddings.npy"
        manifest_file = self.index_dir / "manifest.json"
        if chunks_file.exists() and embeddings_file.exists():
            with chunks_file.open("rb") as file:
                self.chunks = pickle.load(file)
            self.embeddings = np.load(embeddings_file)
        if manifest_file.exists():
            self.manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    def _save_index(self):
        """将当前索引数据持久化到磁盘。

        将分块列表序列化为 pickle 文件，嵌入向量保存为 numpy 文件，
        文件清单保存为 JSON 文件。
        """
        with (self.index_dir / "chunks.pkl").open("wb") as file:
            pickle.dump(self.chunks, file)
        if self.embeddings is not None:
            np.save(self.index_dir / "embeddings.npy", self.embeddings)
        (self.index_dir / "manifest.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def upload_pdf(self, file_storage, filename: str) -> dict:
        """上传并索引一个 PDF 文件。

        将 PDF 文件保存到用户的文库目录，提取文本内容，按页分块后计算
        嵌入向量，并更新索引和文件清单。上传前会检查文件数量和大小限制。

        Args:
            file_storage: 类文件对象（如 Flask 的 ``FileStorage``），需提供
                ``save(path)`` 方法。
            filename: 原始文件名，会经过 ``sanitize_filename`` 清洗。

        Returns:
            该文件的元信息字典，包含 ``num_pages``（页数）、``num_chunks``（分块数）、
            ``upload_time``（上传时间）和 ``size_mb``（文件大小，单位 MB）。

        Raises:
            ValueError: 当超出文件数量上限、文件过大或 PDF 无法提取文本时抛出。
        """
        if len(self.manifest) >= self.rag_settings.max_files_per_user:
            raise ValueError(f"最多上传 {self.rag_settings.max_files_per_user} 个文件")
        safe_name = sanitize_filename(filename)
        pdf_path = self.pdf_dir / safe_name
        file_storage.save(str(pdf_path))
        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        if size_mb > self.rag_settings.max_pdf_size_mb:
            pdf_path.unlink()
            raise ValueError(f"文件过大 ({size_mb:.1f}MB)，最大 {self.rag_settings.max_pdf_size_mb}MB")
        pages_text = self._extract_pdf_text(pdf_path)
        if not pages_text:
            pdf_path.unlink()
            raise ValueError("PDF 无法提取文本（可能是扫描件）")
        new_chunks = self._chunk_pages(safe_name, pages_text)
        new_embeddings = self._embed_texts([chunk["content"] for chunk in new_chunks])
        self.chunks.extend(new_chunks)
        if self.embeddings is not None and len(self.embeddings) > 0:
            self.embeddings = np.vstack([self.embeddings, new_embeddings])
        else:
            self.embeddings = new_embeddings
        self.manifest[safe_name] = {
            "num_pages": len(pages_text),
            "num_chunks": len(new_chunks),
            "upload_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "size_mb": round(size_mb, 2),
        }
        self._save_index()
        return self.manifest[safe_name]

    @staticmethod
    def _extract_pdf_text(pdf_path: Path) -> list[tuple[int, str]]:
        """从 PDF 文件中提取各页文本内容。

        使用 PyMuPDF (fitz) 库逐页提取文本，跳过内容为空的页面。

        Args:
            pdf_path: PDF 文件的路径。

        Returns:
            包含 ``(页码, 文本内容)`` 元组的列表，页码从 1 开始计数。
            仅包含有非空文本的页面。

        Raises:
            ImportError: 当未安装 PyMuPDF 时抛出。
            ValueError: 当 PDF 解析失败时抛出。
        """
        try:
            import fitz
        except ImportError as exc:
            raise ImportError("需要安装 PyMuPDF: pip install PyMuPDF") from exc
        try:
            doc = fitz.open(str(pdf_path))
            pages = [
                (page.number + 1, text)
                for page in doc
                if (text := page.get_text()).strip()
            ]
            doc.close()
            return pages
        except Exception as exc:
            raise ValueError(f"PDF 解析失败: {exc}") from exc

    def _chunk_pages(self, filename: str, pages_text: list[tuple[int, str]]) -> list[dict]:
        """将各页文本按固定窗口大小进行分块。

        使用滑动窗口方式将每一页的文本切分为多个重叠的文本块，
        窗口大小和重叠量由 RAG 配置决定。每个分块包含来源、标题、
        内容和元数据信息。

        Args:
            filename: 清洗后的文件名，用于标记分块的来源。
            pages_text: 由 ``_extract_pdf_text`` 返回的 ``(页码, 文本)`` 列表。

        Returns:
            分块字典列表，每个字典包含 ``source_type``、``title``、``content``、
            ``url``、``score`` 和 ``metadata``（含 ``filename`` 与 ``page``）字段。
        """
        chunks = []
        step = max(self.rag_settings.chunk_size - self.rag_settings.chunk_overlap, 1)
        for page_num, text in pages_text:
            start = 0
            while start < len(text):
                chunk_text = text[start:start + self.rag_settings.chunk_size]
                if chunk_text.strip():
                    chunks.append(
                        {
                            "source_type": "personal",
                            "title": filename,
                            "content": chunk_text.strip(),
                            "url": "",
                            "score": 0.0,
                            "metadata": {"filename": filename, "page": page_num},
                        }
                    )
                start += step
        return chunks

    def delete_file(self, filename: str) -> bool:
        """从个人文库中删除指定文件及其索引数据。

        删除磁盘上的 PDF 文件，移除该文件对应的所有文本分块和嵌入向量，
        并更新文件清单和持久化索引。

        Args:
            filename: 要删除的文件名（清洗后的安全文件名）。

        Returns:
            删除成功返回 True；若文件不在清单中则返回 False。
        """
        if filename not in self.manifest:
            return False
        pdf_path = self.pdf_dir / filename
        if pdf_path.exists():
            pdf_path.unlink()
        keep = [
            index
            for index, chunk in enumerate(self.chunks)
            if chunk["metadata"].get("filename") != filename
        ]
        self.chunks = [self.chunks[index] for index in keep]
        if self.embeddings is not None:
            self.embeddings = self.embeddings[keep] if keep else np.empty((0, self.embeddings.shape[1]))
        del self.manifest[filename]
        self._save_index()
        return True

    def rename_file(self, old_name: str, new_name: str) -> bool:
        """重命名个人文库中的文件。

        更新磁盘上的 PDF 文件名、所有相关分块的元数据以及文件清单，
        新文件名会经过 ``sanitize_filename`` 清洗。

        Args:
            old_name: 当前文件名。
            new_name: 新文件名（会被清洗处理）。

        Returns:
            重命名成功返回 True；若原文件名不在清单中则返回 False。
        """
        if old_name not in self.manifest:
            return False
        safe_new = sanitize_filename(new_name)
        old_path = self.pdf_dir / old_name
        if old_path.exists():
            old_path.rename(self.pdf_dir / safe_new)
        for chunk in self.chunks:
            if chunk["metadata"].get("filename") == old_name:
                chunk["metadata"]["filename"] = safe_new
                chunk["title"] = safe_new
        self.manifest[safe_new] = self.manifest.pop(old_name)
        self._save_index()
        return True

    def list_files(self) -> list[dict]:
        """列出个人文库中的所有文件。

        Returns:
            文件信息字典列表，每个字典包含 ``filename``（文件名）及其
            元信息（``num_pages``、``num_chunks``、``upload_time``、``size_mb``）。
        """
        return [{"filename": filename, **meta} for filename, meta in self.manifest.items()]

    def search(self, query_embedding: np.ndarray, top_k: int = 10) -> list[dict]:
        """基于余弦相似度的向量搜索。

        计算查询向量与所有分块嵌入向量之间的余弦相似度，返回相似度
        最高的 top_k 个分块。

        Args:
            query_embedding: 查询文本的嵌入向量，形状为 ``(dim,)``。
            top_k: 返回的最大结果数量，默认为 10。

        Returns:
            按相似度降序排列的分块字典列表，每个字典在原始分块信息
            基础上额外包含 ``score``（余弦相似度得分）字段。
            若索引为空则返回空列表。
        """
        if self.embeddings is None or len(self.embeddings) == 0:
            return []
        scores = np.dot(self.embeddings, query_embedding) / (
            np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding) + 1e-9
        )
        indices = np.argsort(scores)[::-1][: min(top_k, len(scores))]
        results = []
        for index in indices:
            item = dict(self.chunks[int(index)])
            item["score"] = float(scores[int(index)])
            results.append(item)
        return results

    def _default_embed_texts(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """使用 Jina Embedding API 计算文本嵌入向量。

        调用 Jina 的嵌入服务，将文本列表转换为稠密向量表示。
        模型名称和 API 地址由 RAG 配置决定。

        Args:
            texts: 待嵌入的文本列表。
            batch_size: 批处理大小（当前实现为单次请求，此参数预留扩展用）。

        Returns:
            形状为 ``(len(texts), embedding_dim)`` 的嵌入向量矩阵。

        Raises:
            RuntimeError: 当 JINA_API_KEY 未配置时抛出。
            requests.HTTPError: 当 API 请求失败时抛出。
        """
        api_key = Settings.from_env().jina_api_key
        if not api_key:
            raise RuntimeError("JINA_API_KEY is required")
        response = requests.post(
            self.rag_settings.jina_embedding_url,
            json={
                "model": self.rag_settings.embedding_model,
                "input": texts,
                "task": "retrieval.passage",
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        response.raise_for_status()
        return np.array([item["embedding"] for item in response.json()["data"]])


class PersonalLibraryService:
    """个人文库服务层，为每个用户的个人文库操作提供稳定的接口边界。

    作为 ``PersonalLibrary`` 的代理层，封装底层文库实例，对外提供
    统一的上传、列表、删除、重命名和搜索接口。
    """

    def __init__(self, library: Any):
        """初始化个人文库服务。

        Args:
            library: ``PersonalLibrary`` 实例，作为底层文库操作的委托对象。
        """
        self._library = library

    def upload_pdf(self, file_storage: Any, filename: str) -> dict:
        """上传 PDF 文件到个人文库。

        Args:
            file_storage: 类文件对象（如 Flask 的 ``FileStorage``），需提供
                ``save(path)`` 方法。
            filename: 原始文件名。

        Returns:
            上传文件的元信息字典。
        """
        return self._library.upload_pdf(file_storage, filename)

    def list_files(self) -> list[dict]:
        """列出个人文库中的所有文件。

        Returns:
            文件信息字典列表。
        """
        return self._library.list_files()

    def delete_file(self, filename: str) -> bool:
        """删除个人文库中的指定文件。

        Args:
            filename: 要删除的文件名。

        Returns:
            删除成功返回 True，文件不存在返回 False。
        """
        return self._library.delete_file(filename)

    def rename_file(self, filename: str, new_name: str) -> bool:
        """重命名个人文库中的指定文件。

        Args:
            filename: 当前文件名。
            new_name: 新文件名。

        Returns:
            重命名成功返回 True，文件不存在返回 False。
        """
        return self._library.rename_file(filename, new_name)

    def search(self, query_embedding: Any, top_k: int = 5) -> list[dict]:
        """在个人文库中进行向量相似度搜索。

        Args:
            query_embedding: 查询文本的嵌入向量。
            top_k: 返回的最大结果数量，默认为 5。

        Returns:
            按相似度降序排列的搜索结果列表。
        """
        return self._library.search(query_embedding, top_k=top_k)


__all__ = ["PersonalLibrary", "PersonalLibraryService", "sanitize_filename"]
