"""材料类型注册表

提供材料类型的注册、查询、列举。内置类型在 types/ 包中定义并自动注册；
也支持不落代码的动态类型管理（P5.2）：

- **覆盖** 任何已注册类型（config/types/<type_id>.yaml，diff 式）
- **自定义类型** 全量 YAML 定义（config/custom_types/<type_id>.yaml），
  无需写 Python 即可新增材料类型
- **删除** 内置类型（config/types/_deleted.yaml 标记，磁盘材料目录保留）

新增内置类型仍需新建 Spec 文件并在此调用 register（适合有自定义提示词的场景）。
"""

from typing import Dict, List, Optional

from .models import DocumentTypeSpec
from . import type_config


class DocumentTypeRegistry:
    """材料类型注册表"""

    def __init__(self):
        self._types: Dict[str, DocumentTypeSpec] = {}
        # 内置默认的原始 Spec（未套覆盖），供"恢复默认"热更新用
        self._defaults: Dict[str, DocumentTypeSpec] = {}
        # 自定义类型的基础 Spec（未套覆盖）
        self._custom_base: Dict[str, DocumentTypeSpec] = {}
        # 内置类型中被删除（隐藏）的 type_id
        self._deleted: set = set()

    # ------------------------------------------------------------ 注册
    def register(self, spec: DocumentTypeSpec) -> None:
        """注册一种内置材料类型（同时留档默认）"""
        if spec.type_id in self._types:
            print(f"⚠️ 材料类型 '{spec.type_id}' 已存在，将被覆盖。")
        self._types[spec.type_id] = spec
        self._defaults[spec.type_id] = spec
        print(f"✅ 已注册材料类型: {spec.name} ({spec.type_id})")

    def register_custom(self, type_id: str, data: dict) -> DocumentTypeSpec:
        """注册（或更新）一种自定义类型；返回生效 Spec（含覆盖套用）"""
        base = type_config.build_custom_spec(type_id, data)
        self._custom_base[type_id] = base
        self._types[type_id] = base
        self.refresh_type(type_id)
        print(f"✅ 已注册自定义类型: {base.name} ({type_id})")
        return self._types[type_id]

    def unregister(self, type_id: str) -> None:
        """从注册表移除（自定义删除/内置删除时用；留档不动便于恢复）"""
        self._types.pop(type_id, None)

    # ------------------------------------------------------------ 查询
    def get(self, type_id: str) -> Optional[DocumentTypeSpec]:
        """获取指定材料类型（已套用用户覆盖，若存在）"""
        return self._types.get(type_id)

    def get_default(self, type_id: str) -> Optional[DocumentTypeSpec]:
        """获取未套覆盖的基础 Spec（内置默认 或 自定义全量定义）"""
        return self._defaults.get(type_id) or self._custom_base.get(type_id)

    def list_types(self) -> List[str]:
        """列出所有材料类型 ID"""
        return list(self._types.keys())

    def is_custom(self, type_id: str) -> bool:
        return type_id in self._custom_base

    def list_builtin_ids(self) -> List[str]:
        return list(self._defaults.keys())

    def list_deleted_builtin(self) -> List[str]:
        return sorted(self._deleted)

    def describe(self) -> str:
        """可读的材料类型清单"""
        lines = []
        for tid, spec in self._types.items():
            tag = "自定义" if self.is_custom(tid) else "内置"
            lines.append(f"- {tid}: {spec.name}（{len(spec.sections)} 章，{tag}）")
        return "\n".join(lines)

    # ------------------------------------------------------------ 启动加载
    def apply_overrides_from_disk(self) -> None:
        """启动时统一加载：内置删除标记 → 自定义类型 → 各类型的覆盖"""
        # 1) 内置删除标记：隐藏对应内置类型
        for tid in type_config.list_deleted():
            if tid in self._defaults:
                self._deleted.add(tid)
                self._types.pop(tid, None)
                print(f"🗑 内置类型 '{tid}' 已被用户删除（隐藏）")
            else:
                print(f"⚠️ 删除标记指向未知内置类型 '{tid}'，已忽略")

        # 2) 自定义类型（冲突内置 id 时跳过，保护内置）
        for tid in type_config.list_custom_types():
            if tid in self._defaults:
                print(f"⚠️ 自定义类型 '{tid}' 与内置类型冲突，已跳过")
                continue
            try:
                data = type_config.load_custom_type(tid)
            except type_config.TypeConfigError as e:
                print(f"⚠️ 自定义类型配置非法，已跳过（{e}）")
                continue
            if data:
                base = type_config.build_custom_spec(tid, data)
                self._custom_base[tid] = base
                self._types[tid] = base
                print(f"✅ 已注册自定义类型: {base.name} ({tid})")

        # 3) 覆盖配置（内置+自定义统一套用）
        for type_id in type_config.list_overrides():
            if type_id not in self._types:
                print(f"⚠️ 覆盖配置指向未注册类型 '{type_id}'，已跳过")
                continue
            try:
                override = type_config.load_type_override(type_id)
            except type_config.TypeConfigError as e:
                print(f"⚠️ 覆盖配置非法，已跳过（{e}）")
                continue
            if override:
                base = self.get_default(type_id)
                self._types[type_id] = type_config.apply_override(base, override)
                print(f"🔧 类型 '{type_id}' 已套用用户覆盖配置")

    # ------------------------------------------------------------ 热更新
    def refresh_type(self, type_id: str) -> DocumentTypeSpec:
        """按磁盘当前覆盖状态热更新某类型（保存/删除配置后调用）"""
        base = self.get_default(type_id)
        if base is None:
            raise KeyError(f"未注册的材料类型: {type_id}")
        override = type_config.load_type_override(type_id)
        self._types[type_id] = (
            type_config.apply_override(base, override) if override else base
        )
        return self._types[type_id]

    def delete_type(self, type_id: str) -> str:
        """删除类型：自定义删定义文件；内置加删除标记。返回 'custom'|'builtin'"""
        if type_id in self._custom_base:
            type_config.delete_custom_type(type_id)
            self._custom_base.pop(type_id, None)
            self._types.pop(type_id, None)
            return "custom"
        if type_id in self._defaults:
            type_config.mark_deleted(type_id)
            type_config.delete_type_override(type_id)  # 覆盖随删除清掉
            self._deleted.add(type_id)
            self._types.pop(type_id, None)
            return "builtin"
        raise KeyError(f"未注册的材料类型: {type_id}")

    def restore_builtin(self, type_id: str) -> DocumentTypeSpec:
        """恢复被删除的内置类型"""
        if type_id not in self._defaults:
            raise KeyError(f"未知内置类型: {type_id}")
        type_config.unmark_deleted(type_id)
        self._deleted.discard(type_id)
        return self.refresh_type(type_id)


# 全局注册表单例
_global_registry: Optional[DocumentTypeRegistry] = None


def get_registry() -> DocumentTypeRegistry:
    """获取全局注册表（单例，自动注册内置类型并套用用户配置）"""
    global _global_registry
    if _global_registry is None:
        _global_registry = DocumentTypeRegistry()
        _register_builtin_types(_global_registry)
        _global_registry.apply_overrides_from_disk()
    return _global_registry


def _register_builtin_types(registry: DocumentTypeRegistry) -> None:
    """注册内置材料类型"""
    from .types.kpi_plan import KpiPlanSpec
    from .types.report import ReportSpec
    from .types.work_summary import WorkSummarySpec

    registry.register(WorkSummarySpec)
    registry.register(ReportSpec)
    registry.register(KpiPlanSpec)
