"""材料类型注册表

提供材料类型的注册、查询、列举。内置类型在 types/ 包中定义并自动注册。
新增材料类型只需新建一个 Spec 文件并在此调用 register，无需改动核心代码。
"""

from typing import Dict, List, Optional

from .models import DocumentTypeSpec


class DocumentTypeRegistry:
    """材料类型注册表"""

    def __init__(self):
        self._types: Dict[str, DocumentTypeSpec] = {}

    def register(self, spec: DocumentTypeSpec) -> None:
        """注册一种材料类型"""
        if spec.type_id in self._types:
            print(f"⚠️ 材料类型 '{spec.type_id}' 已存在，将被覆盖。")
        self._types[spec.type_id] = spec
        print(f"✅ 已注册材料类型: {spec.name} ({spec.type_id})")

    def get(self, type_id: str) -> Optional[DocumentTypeSpec]:
        """获取指定材料类型"""
        return self._types.get(type_id)

    def list_types(self) -> List[str]:
        """列出所有材料类型 ID"""
        return list(self._types.keys())

    def describe(self) -> str:
        """可读的材料类型清单"""
        lines = []
        for tid, spec in self._types.items():
            lines.append(f"- {tid}: {spec.name}（{len(spec.sections)} 章，{spec.paradigm} 范式）")
        return "\n".join(lines)


# 全局注册表单例
_global_registry: Optional[DocumentTypeRegistry] = None


def get_registry() -> DocumentTypeRegistry:
    """获取全局注册表（单例，自动注册内置类型）"""
    global _global_registry
    if _global_registry is None:
        _global_registry = DocumentTypeRegistry()
        _register_builtin_types(_global_registry)
    return _global_registry


def _register_builtin_types(registry: DocumentTypeRegistry) -> None:
    """注册内置材料类型"""
    from .types.kpi_plan import KpiPlanSpec
    from .types.report import ReportSpec
    from .types.work_summary import WorkSummarySpec

    registry.register(WorkSummarySpec)
    registry.register(ReportSpec)
    registry.register(KpiPlanSpec)
