"""材料管理器：参考材料导入与检索的统一入口

提供两种参考方式：
1. 主动注入（get_relevant）：编排器在每节撰写前按章节取相关片段，拼进 prompt
2. 被动召回（search / recall）：注册为 Tool，ReAct 写作中按需检索

两种方式互补，确保 Agent 既能"必定看到"范文，又能"按需细查"。
"""

from pathlib import Path
from typing import List, Optional

from .loader import scan_materials
from .local_backend import LocalMaterialBackend
from .rag_backend import RAGBackend

# 检索结果为空时的占位
_EMPTY_HINT = "（暂无相关参考材料）"


class MaterialManager:
    """参考材料管理器

    Args:
        mode: 后端模式
            - "auto": 优先 RAG，不可用时自动降级为本地后端（默认推荐）
            - "rag":  强制 RAG（Qdrant + Embedding）
            - "local": 强制本地内存后端（纯字面匹配，零外部依赖）
        kb_path: RAG 本地索引目录
        namespace: RAG 命名空间（隔离不同材料库）
    """

    def __init__(
        self,
        mode: str = "auto",
        kb_path: Optional[str] = None,
        namespace: str = "reportwriter",
    ):
        self.mode = mode
        self.namespace = namespace

        if mode == "local":
            self.backend = LocalMaterialBackend(namespace=namespace)
            self._effective_mode = "local"
        elif mode == "rag":
            self.backend = RAGBackend(kb_path=kb_path, namespace=namespace)
            self._effective_mode = "rag"
        elif mode == "auto":
            rag = RAGBackend(kb_path=kb_path, namespace=namespace)
            if rag.ready:
                self.backend = rag
                self._effective_mode = "rag"
            else:
                print(
                    f"▸️  RAG 后端不可用（{rag.error}），"
                    "已降级为本地参考材料模式（字面匹配）"
                )
                self.backend = LocalMaterialBackend(namespace=namespace)
                self._effective_mode = "local"
        else:
            raise ValueError(
                f"未知材料后端模式: {mode}（支持 auto / rag / local）"
            )
        self._ingested_count = 0

    @property
    def effective_mode(self) -> str:
        """实际生效的后端模式（auto 下可能降级为 local）"""
        return self._effective_mode

    @property
    def ready(self) -> bool:
        """材料后端是否就绪"""
        return getattr(self.backend, "ready", False)

    @property
    def error(self) -> Optional[str]:
        """材料后端未就绪时的错误信息"""
        return getattr(self.backend, "error", None)

    def ingest(self, path: str) -> dict:
        """导入文件或目录下的所有参考材料

        Args:
            path: 单个文件路径或目录路径

        Returns:
            {"total": N, "success": M, "details": [...]}
        """
        root = Path(path)
        if root.is_file():
            files = [root]
        else:
            files = scan_materials(path)

        details = []
        success = 0
        for f in files:
            result = self.backend.ingest_file(str(f))
            ok = "失败" not in result
            if ok:
                success += 1
            details.append({"file": str(f), "result": result, "ok": ok})

        self._ingested_count += success
        print(
            f"▸ 参考材料导入完成（{self._effective_mode} 模式）: "
            f"{success}/{len(files)} 成功"
        )
        return {"total": len(files), "success": success, "details": details}

    def get_relevant(self, query: str, top_k: int = 3) -> str:
        """主动注入：按 query 召回 Top-K 片段，格式化为可拼入 prompt 的文本"""
        if not query or not query.strip():
            return _EMPTY_HINT
        result = self.backend.search(query, top_k=top_k)
        if not result or "失败" in result or "无" in result[:10]:
            return _EMPTY_HINT
        # 截断超长结果，避免撑爆上下文
        max_chars = 2000
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...(参考材料已截断)"
        return result

    def search(self, query: str, top_k: int = 3) -> str:
        """被动召回：供 recall_material 工具调用，与 get_relevant 同源"""
        return self.get_relevant(query, top_k=top_k)

    def stats(self) -> str:
        """材料库统计"""
        return self.backend.stats()
