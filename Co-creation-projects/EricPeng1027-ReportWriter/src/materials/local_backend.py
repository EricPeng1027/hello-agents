"""本地兜底参考材料后端（纯标准库实现）

当 RAG 后端不可用（未装 qdrant-client / 未配 QDRANT_URL / Embedding 未就绪）时，
用本后端保证"参考材料"能力仍可用：

- ingest_file: 直接读取纯文本（md/txt/json/html/csv），docx 尝试用 python-docx
- 按定长分块存入内存
- search: 基于字面量 / 关键词重叠的简易打分检索

优点：零外部依赖、无需向量库、即开即用。
缺点：只做字面匹配，无语义召回，相关度弱于 RAG。
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 与 RAG 后端保持一致的直读格式
_TEXT_EXTS = {".md", ".txt", ".json", ".html", ".htm", ".csv", ".log"}


class LocalMaterialBackend:
    """内存级本地参考材料后端（接口与 RAGBackend 对齐）"""

    def __init__(self, namespace: str = "reportwriter", **kwargs):
        self.namespace = namespace
        # 分块库: [(chunk_text, source_name, chunk_index)]
        self._chunks: List[Tuple[str, str, int]] = []
        self._ingested_files: List[str] = []
        # 本地后端总是"就绪"
        self._ready = True
        self._init_error: Optional[str] = None

    # ------------------------------------------------------------------ ready
    @property
    def ready(self) -> bool:
        return True

    @property
    def error(self) -> Optional[str]:
        return None

    # ------------------------------------------------------------------ read
    def _read_text(self, file_path: str) -> str:
        p = Path(file_path)
        ext = p.suffix.lower()
        if ext in _TEXT_EXTS:
            return p.read_text(encoding="utf-8", errors="ignore")
        if ext == ".docx":
            try:
                from docx import Document

                doc = Document(str(p))
                return "\n".join(par.text for par in doc.paragraphs)
            except Exception:
                return ""
        # 其余二进制格式本地模式无法解析
        return ""

    def _chunk(self, text: str, size: int = 800, overlap: int = 100) -> List[str]:
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
        p = Path(file_path)
        # 幂等导入：同一路径不重复分块（web 场景会反复 reingest 同一目录）
        if str(p) in self._ingested_files:
            return f"已导入，跳过: {p.name}"
        text = self._read_text(file_path)
        if not text.strip():
            return f"文件内容为空或本地模式不支持解析: {p.name}"
        chunks = self._chunk(text)
        if not chunks:
            return f"分块为空: {p.name}"
        base = len(self._chunks)
        for idx, ch in enumerate(chunks):
            self._chunks.append((ch, p.name, base + idx))
        self._ingested_files.append(str(p))
        return f"已导入 {len(chunks)} 个片段（本地模式）: {p.name}"

    # ------------------------------------------------------------------ search
    def search(self, query: str, top_k: int = 3) -> str:
        if not query or not query.strip() or not self._chunks:
            return ""

        terms = self._extract_terms(query)
        if not terms:
            return ""

        scored: List[Tuple[float, Tuple[str, str, int]]] = []
        for chunk in self._chunks:
            text = chunk[0]
            score = 0.0
            for t in terms:
                if not t:
                    continue
                if t in text:
                    # 完整命中：长词权重更高
                    score += len(t)
                else:
                    # 部分命中：词内 2-gram 覆盖率（缓解整词未命中问题）
                    grams = {t[i : i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else {t}
                    if grams:
                        hit = sum(1 for g in grams if g in text)
                        score += 0.5 * len(t) * (hit / len(grams))
            if score > 0:
                scored.append((score, chunk))

        if not scored:
            # 字面检索无命中时，兜底返回最新导入的片段（本地模式只追求"有参考"）
            fallback = self._chunks[-top_k:]
            return "\n\n---\n\n".join(
                f"【来源: {source}】\n{text}" for text, source, _ in fallback
            )

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, (text, source, idx) in scored[:top_k]:
            results.append(f"【来源: {source}】\n{text}")
        return "\n\n---\n\n".join(results)

    @staticmethod
    def _extract_terms(query: str) -> List[str]:
        """从 query 提取检索词：连续中文串 + 英文单词"""
        terms: List[str] = []
        # 连续中文（2 字及以上）
        terms.extend(re.findall(r"[一-鿿]{2,}", query))
        # 英文单词
        terms.extend(re.findall(r"[A-Za-z]{2,}", query))
        # 去重保序
        seen = set()
        uniq = []
        for t in terms:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return uniq

    # ------------------------------------------------------------------ stats
    def stats(self) -> str:
        return (
            f"本地材料库（{self.namespace}）: "
            f"{len(self._ingested_files)} 个文件, {len(self._chunks)} 个片段"
        )
