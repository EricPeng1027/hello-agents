"""RAG 后端：直接基于 qdrant-client + OpenAI 兼容 Embedding 实现

说明：已安装的 hello-agents==1.0.0 的 hello_agents.tools 并不提供 RAGTool
（RAGTool 仅存在于教程仓库的 vendored 副本中，未随 PyPI 包发布）。
因此这里直接用 qdrant-client + openai 兼容接口自建轻量 RAG，保证自包含可运行。

能力：
- ingest_file: 读取文件 → 纯文本（md/txt/json/html 直读；docx/pdf 走 markitdown）
- 分块 + 向量化 + 写入 Qdrant（带 document_id/source 元数据）
- search: query 向量化 → Qdrant 语义检索 → 返回拼接片段
"""

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import get_settings

# 文本类直读格式
_TEXT_EXTS = {".md", ".txt", ".json", ".html", ".htm", ".csv", ".log"}


class RAGBackend:
    """基于 Qdrant + Embedding 的 RAG 后端"""

    def __init__(self, kb_path: Optional[str] = None, namespace: str = "reportwriter"):
        self.settings = get_settings()
        self.kb_path = kb_path or self.settings.material_kb_dir
        self.namespace = namespace
        self.collection = f"{self.settings.qdrant_collection}_{namespace}"

        self._client = None
        self._embed_client = None
        self._ready = False
        self._init_error: Optional[str] = None
        self._init()

    # ------------------------------------------------------------------ init
    def _init(self):
        """惰性初始化 Qdrant 客户端与 Embedding 客户端，失败不抛错只标记"""
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http import models as qm

            url = self.settings.qdrant_url
            api_key = self.settings.qdrant_api_key
            if not url:
                self._init_error = "QDRANT_URL 未配置"
                print(f"▸️  RAG 后端未就绪：{self._init_error}")
                return

            if api_key:
                self._client = QdrantClient(url=url, api_key=api_key, timeout=self.settings.qdrant_timeout)
            else:
                # 本地无密钥
                self._client = QdrantClient(url=url, timeout=self.settings.qdrant_timeout)

            # Embedding 客户端（OpenAI 兼容）
            self._embed_client = self._build_embed_client()
            if self._embed_client is None:
                self._init_error = "Embedding 客户端未配置（EMBED_API_KEY/EMBED_BASE_URL 或 DASHSCOPE_API_KEY）"
                print(f"▸️  RAG 后端未就绪：{self._init_error}")
                return

            # 确保集合存在
            self._ensure_collection()
            self._ready = True
            print(f"▸ RAG 后端就绪：collection={self.collection}, dim={self.settings.qdrant_vector_size}")
        except Exception as e:
            self._init_error = f"RAG 初始化失败：{e}"
            print(f"▸️  RAG 后端未就绪：{self._init_error}（将降级为无召回模式）")

    def _build_embed_client(self):
        """构建 OpenAI 兼容的 Embedding 客户端"""
        try:
            from openai import OpenAI
        except Exception as e:
            print(f"▸️  未安装 openai 库：{e}")
            return None

        s = self.settings
        api_key = s.embed_api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = s.embed_base_url or os.getenv("DASHSCOPE_BASE_URL")
        # dashscope 默认
        if not base_url and (s.embed_model_type == "dashscope" or os.getenv("DASHSCOPE_API_KEY")):
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        if not api_key or not base_url:
            return None
        return OpenAI(api_key=api_key, base_url=base_url, timeout=60)

    def _ensure_collection(self):
        """确保 Qdrant 集合存在"""
        from qdrant_client.http import models as qm

        try:
            self._client.get_collection(self.collection)
        except Exception:
            # 集合不存在则创建
            self._client.recreate_collection(
                collection_name=self.collection,
                vectors_config=qm.VectorParams(
                    size=self.settings.qdrant_vector_size,
                    distance=getattr(qm.Distance, self.settings.qdrant_distance.upper(), qm.Distance.COSINE),
                ),
            )
            print(f"▸ 已创建 Qdrant 集合：{self.collection}")

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> Optional[str]:
        return self._init_error

    # ------------------------------------------------------------------ embed
    def _embed(self, texts: List[str]) -> List[List[float]]:
        """批量向量化"""
        model = self.settings.embed_model_name or "text-embedding-v3"
        resp = self._embed_client.embeddings.create(model=model, input=texts)
        return [d.embedding for d in resp.data]

    # ------------------------------------------------------------------ read
    def _read_text(self, file_path: str) -> str:
        """读取文件为纯文本"""
        p = Path(file_path)
        ext = p.suffix.lower()
        if ext in _TEXT_EXTS:
            try:
                return p.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                return f""
        # 二进制格式：优先用 markitdown
        try:
            from markitdown import MarkItDown

            md = MarkItDown()
            result = md.convert(str(p))
            return getattr(result, "text_content", "") or ""
        except Exception as e:
            print(f"▸️  无法解析 {p.name}（未安装 markitdown 或格式不支持）: {e}")
            return ""

    def _chunk(self, text: str, size: int = 800, overlap: int = 100) -> List[str]:
        """简单定长分块"""
        if not text:
            return []
        chunks = []
        i = 0
        while i < len(text):
            chunks.append(text[i : i + size])
            i += size - overlap
        return chunks

    # ------------------------------------------------------------------ ingest
    def ingest_file(self, file_path: str) -> str:
        """导入单个文件：读取 → 分块 → 向量化 → 写入 Qdrant"""
        if not self._ready:
            return f"RAG 未就绪：{self._init_error}"
        p = Path(file_path)
        text = self._read_text(file_path)
        if not text.strip():
            return f"文件内容为空或无法解析: {p.name}"

        chunks = self._chunk(text)
        if not chunks:
            return f"分块为空: {p.name}"

        try:
            vectors = self._embed(chunks)
        except Exception as e:
            return f"向量化失败 {p.name}: {e}"

        from qdrant_client.http import models as qm

        points = [
            qm.PointStruct(
                # Qdrant point ID 必须是无符号整数或 UUID,用 UUID5 保证同文件同片段可重复
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{self.namespace}_{p.stem}_{idx}")),
                vector=vec,
                payload={
                    "text": chunk,
                    "source": p.name,
                    "document_id": p.stem,
                    "namespace": self.namespace,
                    "chunk_index": idx,
                },
            )
            for idx, (chunk, vec) in enumerate(zip(chunks, vectors))
        ]
        try:
            self._client.upsert(collection_name=self.collection, points=points)
            return f"已导入 {len(points)} 个片段: {p.name}"
        except Exception as e:
            return f"写入 Qdrant 失败 {p.name}: {e}"

    # ------------------------------------------------------------------ search
    def search(self, query: str, top_k: int = 3) -> str:
        """语义检索"""
        if not self._ready:
            return ""
        if not query or not query.strip():
            return ""
        try:
            vec = self._embed([query])[0]
        except Exception as e:
            return f""

        try:
            hits = self._client.search(
                collection_name=self.collection,
                query_vector=vec,
                limit=top_k,
            )
        except Exception as e:
            return f""

        results = []
        for h in hits:
            payload = h.payload or {}
            text = payload.get("text", "")
            source = payload.get("source", "")
            if text:
                results.append(f"【来源: {source}】\n{text}")
        return "\n\n---\n\n".join(results)

    # ------------------------------------------------------------------ stats
    def stats(self) -> str:
        if not self._ready:
            return f"RAG 未就绪：{self._init_error}"
        try:
            info = self._client.get_collection(self.collection)
            return f"集合: {self.collection}, 点数: {info.points_count}"
        except Exception as e:
            return f"统计失败: {e}"
