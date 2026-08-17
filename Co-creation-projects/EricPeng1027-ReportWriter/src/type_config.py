"""材料类型结构覆盖配置（YAML 层）

内置类型（src/types/*.py）是默认值，但**类型的完整生命周期都可配置**：

1. **覆盖** `config/types/<type_id>.yaml` —— 只写想改的字段（diff 式）：

       name: 年度工作总结          # 连类型名都能改
       system_prompt: "..."
       material_role_hint: "..."
       word_count_total: 2300
       sections:                  # 整体替换章节骨架
         - {key: overview, title: 总体概述, target_words: 300, hints: "..."}

2. **自定义类型** `config/custom_types/<type_id>.yaml` —— 全量定义一种新类型
   （范式/工具/材料模式沿用系统默认：plan_solve + recall_material + rag/auto），
   额外必填 `name`；提示词未配置时由默认模板派生。

3. **删除内置类型** `config/types/_deleted.yaml` —— 列出要隐藏的内置 type_id，
   其磁盘材料目录保留但不再注册。

加载时机：get_registry() 注册内置后统一套用；web 保存后立即热更新。
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import DocumentTypeSpec, SectionSpec
from .prompts import (
    get_planner_prompts,
    get_drafter_react_prompt,
    get_drafter_task_template,
    get_reviewer_prompts,
)

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "types"
_CUSTOM_DIR = Path(__file__).resolve().parent.parent / "config" / "custom_types"
_DELETED_FILE = "deleted"  # config/types/_deleted.yaml 的语义键

# 允许覆盖的标量字段（sections 单独处理）
SCALAR_FIELDS = ("name", "system_prompt", "material_role_hint", "word_count_total")

# 章节级允许覆盖的字段
SECTION_FIELDS = ("key", "title", "required", "target_words", "hints")

# 自定义类型的默认写作专家角色（用户可在配置里覆盖 system_prompt）
DEFAULT_SYSTEM_PROMPT = (
    "你是一位资深的材料撰写专家，擅长把零散的素材梳理为结构清晰、重点突出、"
    "语言专业得体的书面材料。"
)

_TYPE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class TypeConfigError(ValueError):
    """类型覆盖配置非法（格式错/字段错/章节结构错）"""


# ------------------------------------------------------------------ 路径/通用
def config_dir() -> Path:
    """覆盖配置目录（不存在则创建）"""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return _CONFIG_DIR


def custom_dir() -> Path:
    """自定义类型目录（不存在则创建）"""
    _CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    return _CUSTOM_DIR


def override_path(type_id: str) -> Path:
    return config_dir() / f"{type_id}.yaml"


def custom_path(type_id: str) -> Path:
    return custom_dir() / f"{type_id}.yaml"


def _deleted_path() -> Path:
    return config_dir() / "_deleted.yaml"


def validate_type_id(type_id: str) -> str:
    """type_id 形态校验（小写字母/数字/下划线，字母开头）"""
    if not _TYPE_ID_RE.match(type_id or ""):
        raise TypeConfigError(
            "type_id 必须以小写字母开头，只能含小写字母/数字/下划线（≤40 字符）"
        )
    return type_id


def has_override(type_id: str) -> bool:
    return override_path(type_id).is_file()


def list_overrides() -> List[str]:
    """列出当前存在覆盖配置的 type_id"""
    d = config_dir()
    return sorted(p.stem for p in d.glob("*.yaml") if not p.stem.startswith("_"))


def is_custom_type(type_id: str) -> bool:
    return custom_path(type_id).is_file()


# ------------------------------------------------------------------ 覆盖（内置/自定义通用）
def load_type_override(type_id: str) -> Optional[Dict[str, Any]]:
    """读取某类型的覆盖配置（不存在返回 None；非法抛 TypeConfigError）"""
    return _load_yaml(override_path(type_id), required=False)


def save_type_override(type_id: str, payload: Dict[str, Any]) -> Path:
    """校验并写回覆盖配置（全量替换该类型的 YAML）"""
    clean = _clean_payload(payload, allow_partial=True)
    path = override_path(type_id)
    path.write_text(
        yaml.safe_dump(clean, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def delete_type_override(type_id: str) -> bool:
    """删除覆盖配置（回退默认定义）。返回是否有文件被删。"""
    path = override_path(type_id)
    if path.is_file():
        path.unlink()
        return True
    return False


# ------------------------------------------------------------------ 自定义类型
def list_custom_types() -> List[str]:
    """列出自定义类型的 type_id（按文件名字典序）"""
    d = custom_dir()
    return sorted(p.stem for p in d.glob("*.yaml"))


def load_custom_type(type_id: str) -> Optional[Dict[str, Any]]:
    """读取自定义类型定义（不存在返回 None；非法抛 TypeConfigError）"""
    data = _load_yaml(custom_path(type_id), required=False)
    if data is None:
        return None
    if not data.get("name") or not isinstance(data.get("name"), str):
        raise TypeConfigError(f"自定义类型 '{type_id}' 缺少合法的 name")
    if not data.get("sections"):
        raise TypeConfigError(f"自定义类型 '{type_id}' 必须定义 sections")
    _validate_sections(data["sections"])
    return data


def save_custom_type(type_id: str, payload: Dict[str, Any]) -> Path:
    """创建/更新自定义类型（全量写回；name 与 sections 必填）"""
    validate_type_id(type_id)
    clean = _clean_payload(payload, allow_partial=False)
    if not clean.get("name"):
        raise TypeConfigError("自定义类型必须提供 name（显示名称）")
    if not clean.get("sections"):
        raise TypeConfigError("自定义类型必须提供 sections（章节骨架）")
    _validate_sections(clean["sections"])
    path = custom_path(type_id)
    path.write_text(
        yaml.safe_dump(clean, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def delete_custom_type(type_id: str) -> bool:
    """删除自定义类型定义文件（同时清掉它的覆盖文件）。返回是否有文件被删。"""
    removed = False
    path = custom_path(type_id)
    if path.is_file():
        path.unlink()
        removed = True
    delete_type_override(type_id)
    return removed


def build_custom_spec(type_id: str, data: Dict[str, Any]) -> DocumentTypeSpec:
    """把自定义类型定义构建为 DocumentTypeSpec（机制字段用系统默认）"""
    return DocumentTypeSpec(
        type_id=type_id,
        name=data["name"],
        paradigm="plan_solve",
        system_prompt=data.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
        custom_prompts={
            "planner": get_planner_prompts()["planner"],
            "executor": get_planner_prompts()["executor"],
            "drafter_react": get_drafter_react_prompt(),
            "drafter_task": get_drafter_task_template(),
            "reviewer": get_reviewer_prompts(),
        },
        sections=[
            SectionSpec(
                key=s["key"],
                title=s["title"],
                required=bool(s.get("required", True)),
                target_words=int(s.get("target_words", 400)),
                hints=s.get("hints", ""),
            )
            for s in data["sections"]
        ],
        word_count_total=int(data.get("word_count_total", 2000)),
        tools=["recall_material"],
        review_enabled=True,
        output_formats=["markdown", "docx"],
        material_mode="rag",
        material_top_k=3,
        material_role_hint=data.get("material_role_hint", ""),
        material_base_dir=type_id,  # 材料目录按类型隔离：data/<type_id>/facts|style
    )


# ------------------------------------------------------------------ 内置删除标记
def list_deleted() -> List[str]:
    """被隐藏的内置类型 type_id 列表"""
    path = _deleted_path()
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data]


def mark_deleted(type_id: str) -> None:
    deleted = list_deleted()
    if type_id not in deleted:
        deleted.append(type_id)
        _deleted_path().write_text(
            yaml.safe_dump(deleted, allow_unicode=True), encoding="utf-8"
        )


def unmark_deleted(type_id: str) -> None:
    deleted = [t for t in list_deleted() if t != type_id]
    path = _deleted_path()
    if deleted:
        path.write_text(
            yaml.safe_dump(deleted, allow_unicode=True), encoding="utf-8"
        )
    elif path.is_file():
        path.unlink()


# ------------------------------------------------------------------ 内部工具
def _load_yaml(path: Path, required: bool) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TypeConfigError(f"{path.name} 不是合法 YAML: {e}")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TypeConfigError(f"{path.name} 顶层必须是映射（字段: 值）")
    return data


def _clean_payload(payload: Dict[str, Any], allow_partial: bool) -> Dict[str, Any]:
    """只保留受支持字段并校验；allow_partial=False 时要求 name+sections"""
    if not isinstance(payload, dict):
        raise TypeConfigError("配置必须是映射")
    clean: Dict[str, Any] = {}
    for f in SCALAR_FIELDS:
        if f in payload and payload[f] is not None:
            clean[f] = payload[f]
    if "sections" in payload and payload["sections"] is not None:
        clean["sections"] = payload["sections"]
    _validate_payload(clean, source="payload")
    return clean


def _validate_payload(data: Dict[str, Any], source: str) -> None:
    """校验覆盖载荷；非法抛 TypeConfigError"""
    for key in data:
        if key not in SCALAR_FIELDS and key != "sections":
            # 未知字段：不报错（前向兼容），仅忽略 —— save 时已过滤，这里只告警
            print(f"⚠️ 类型配置含未知字段 '{key}'（{source}），已忽略")
    if "word_count_total" in data:
        v = data["word_count_total"]
        if not isinstance(v, int) or v <= 0:
            raise TypeConfigError("word_count_total 必须是正整数")
    for f in ("name", "system_prompt", "material_role_hint"):
        if f in data and not isinstance(data[f], str):
            raise TypeConfigError(f"{f} 必须是字符串")
    if "sections" in data:
        _validate_sections(data["sections"])


def _validate_sections(sections: Any) -> None:
    if not isinstance(sections, list) or not sections:
        raise TypeConfigError("sections 必须是非空列表")
    seen = set()
    for i, s in enumerate(sections, 1):
        if not isinstance(s, dict):
            raise TypeConfigError(f"第 {i} 章必须是映射")
        unknown = set(s) - set(SECTION_FIELDS)
        if unknown:
            print(f"⚠️ 第 {i} 章含未知字段 {sorted(unknown)}，已忽略")
        key = s.get("key")
        if not key or not isinstance(key, str):
            raise TypeConfigError(f"第 {i} 章缺少合法的 key")
        if key in seen:
            raise TypeConfigError(f"章节 key 重复: '{key}'")
        seen.add(key)
        if not s.get("title"):
            raise TypeConfigError(f"章节 '{key}' 缺少 title")
        tw = s.get("target_words", 400)
        if not isinstance(tw, int) or tw <= 0:
            raise TypeConfigError(f"章节 '{key}' 的 target_words 必须是正整数")
        if "hints" in s and not isinstance(s["hints"], str):
            raise TypeConfigError(f"章节 '{key}' 的 hints 必须是字符串")


def apply_override(spec: DocumentTypeSpec, override: Dict[str, Any]) -> DocumentTypeSpec:
    """把覆盖配置套用到 Spec 上，返回新实例（不改原对象）

    sections 整体替换；name/system_prompt 等标量直接覆盖。
    custom_prompts/paradigm 等机制字段不动。
    """
    import copy

    new_spec = copy.copy(spec)
    new_spec.sections = list(spec.sections)
    for f in SCALAR_FIELDS:
        if f in override:
            setattr(new_spec, f, override[f])
    if "sections" in override:
        new_spec.sections = [
            SectionSpec(
                key=s["key"],
                title=s["title"],
                required=bool(s.get("required", True)),
                target_words=int(s.get("target_words", 400)),
                hints=s.get("hints", ""),
            )
            for s in override["sections"]
        ]
    return new_spec
