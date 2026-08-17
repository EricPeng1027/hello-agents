"""材料管理器：参考材料导入与检索的统一入口

参考方式：
1. 主动注入（get_relevant）：编排器在每节撰写前按章节取相关片段，拼进 prompt
2. 被动召回（search / recall）：注册为 Tool，ReAct 写作中按需检索

材料角色（scope）：
- "facts": 事实材料——内容必须基于它（数据/事实/口径），决定"写什么"
- "style": 风格材料——仅参考写法与结构，不得照搬内容，决定"怎么写"
-  None  : 不区分角色（向后兼容，单库模式）
"""

from pathlib import Path
from typing import Dict, List, Optional

from .loader import scan_materials
from .local_backend import LocalMaterialBackend
from .rag_backend import RAGBackend

# 检索结果为空时的占位
_EMPTY_HINT = "（暂无相关参考材料）"

# 材料角色常量
SCOPE_FACTS = "facts"
SCOPE_STYLE = "style"


class MaterialManager:
    """参考材料管理器

    Args:
        mode: 后端模式
            - "auto": 优先 RAG，不可用时自动降级为本地后端（默认推荐）
            - "rag":  强制 RAG（Qdrant + Embedding）
            - "local": 强制本地内存后端（纯字面匹配，零外部依赖）
        kb_path: RAG 本地索引目录
        namespace: RAG 命名空间（隔离不同材料库）

    分角色使用时，内部为每个 scope 维护一个独立后端实例
    （namespace 派生为 f"{namespace}_{scope}"，RAG/local 均隔离）。
    """

    def __init__(
        self,
        mode: str = "auto",
        kb_path: Optional[str] = None,
        namespace: str = "reportwriter",
    ):
        self.mode = mode
        self.namespace = namespace
        self._kb_path = kb_path

        # 默认（无角色）后端，保持向后兼容
        self.backend = self._build_backend(namespace)
        self._effective_mode = self._resolve_mode(self.backend)

        # 角色后端（惰性创建）
        self._scoped_backends: Dict[str, object] = {}

        self._ingested_count = 0

    # ------------------------------------------------------------------ build
    def _build_backend(self, namespace: str):
        if self.mode == "local":
            return LocalMaterialBackend(namespace=namespace)
        if self.mode == "rag":
            return RAGBackend(kb_path=self._kb_path, namespace=namespace)
        if self.mode == "auto":
            rag = RAGBackend(kb_path=self._kb_path, namespace=namespace)
            if rag.ready:
                return rag
            print(
                f"▸️  RAG 后端不可用（{rag.error}），"
                f"已降级为本地参考材料模式（字面匹配）[ns={namespace}]"
            )
            return LocalMaterialBackend(namespace=namespace)
        raise ValueError(f"未知材料后端模式: {self.mode}（支持 auto / rag / local）")

    @staticmethod
    def _resolve_mode(backend) -> str:
        return "local" if isinstance(backend, LocalMaterialBackend) else "rag"

    def _get_backend(self, scope: Optional[str] = None):
        """按角色取后端实例（惰性创建并缓存）

        scope 可含类型命名空间，如 "work_summary:facts"（按类型+角色隔离），
        派生后端命名空间为 f"{namespace}_{scope}"（":" 替换为 "_"）。
        """
        if not scope:
            return self.backend
        if scope not in self._scoped_backends:
            ns_suffix = scope.replace(":", "_")
            self._scoped_backends[scope] = self._build_backend(
                f"{self.namespace}_{ns_suffix}"
            )
        return self._scoped_backends[scope]

    # ------------------------------------------------------------------ props
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

    # ------------------------------------------------------------------ ingest
    def ingest(self, path: str, scope: Optional[str] = None) -> dict:
        """导入文件或目录下的所有参考材料

        Args:
            path: 单个文件路径或目录路径
            scope: 材料角色（"facts"/"style"/None），决定写入哪个库

        Returns:
            {"total": N, "success": M, "details": [...]}
        """
        root = Path(path)
        if root.is_file():
            files = [root]
        else:
            files = scan_materials(path)

        backend = self._get_backend(scope)
        details = []
        success = 0
        for f in files:
            result = backend.ingest_file(str(f))
            ok = "失败" not in result and "为空" not in result
            if ok:
                success += 1
            details.append({"file": str(f), "result": result, "ok": ok})

        self._ingested_count += success
        scope_label = f"[{scope}] " if scope else ""
        print(
            f"▸ 参考材料导入完成{scope_label}（{self._effective_mode} 模式）: "
            f"{success}/{len(files)} 成功"
        )
        return {"total": len(files), "success": success, "details": details}

    # ------------------------------------------------------------------ search
    def get_relevant(
        self, query: str, top_k: int = 3, scope: Optional[str] = None
    ) -> str:
        """主动注入：按 query 召回 Top-K 片段，格式化为可拼入 prompt 的文本"""
        if not query or not query.strip():
            return _EMPTY_HINT
        backend = self._get_backend(scope)
        result = backend.search(query, top_k=top_k)
        if not result or "失败" in result:
            return _EMPTY_HINT
        # 截断超长结果，避免撑爆上下文
        max_chars = 2000
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...(参考材料已截断)"
        return result

    def search(self, query: str, top_k: int = 3, scope: Optional[str] = None) -> str:
        """被动召回：供 recall_material 工具调用，与 get_relevant 同源"""
        return self.get_relevant(query, top_k=top_k, scope=scope)

    def stats(self, scope: Optional[str] = None) -> str:
        """材料库统计"""
        return self._get_backend(scope).stats()
