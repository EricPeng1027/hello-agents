"""recall_material 检索工具

作为 DraftingAgent 的本地工具，在 ReAct 撰写过程中可被调用，
从用户导入的参考材料库中检索相关片段。

支持两种用法：
- recall_material[关键词]                → 检索默认库（未分 facts/style 时使用）
- recall_material[scope:facts][关键词]   → 只检索事实材料库
- recall_material[scope:style][关键词]   → 只检索风格材料库

Drafter 的 ToolExecutor 接受 (name, description, func(str)->str)，
故这里提供一个工厂函数生成可调用对象。
"""

import re
from typing import Callable, Optional

from ..materials.manager import MaterialManager


def build_recall_material_tool(
    material_manager: MaterialManager,
    top_k: int = 3,
    default_scope: Optional[str] = None,
) -> Callable[[str], str]:
    """构建 recall_material 工具函数

    Args:
        material_manager: 材料管理器
        top_k: 每次召回的片段数
        default_scope: 默认检索库（"facts"/"style"/None），
            未指定 scope: 前缀时使用；None 表示检索默认（无角色）库

    Returns:
        一个 (query: str) -> str 的可调用函数
    """

    def recall_material(query: str) -> str:
        scope, real_query = _parse_scope_prefix(query) or (default_scope, query)
        result = material_manager.search(real_query, top_k=top_k, scope=scope)
        scope_label = f"[{scope}] " if scope else ""
        return result or f"（{scope_label}未检索到相关参考材料）"

    return recall_material


def _parse_scope_prefix(text: str):
    """解析 'scope:facts][关键词' 或 'scope:style][关键词' 前缀

    Returns:
        (scope, query) 或 None（未带 scope 前缀）
    """
    m = re.match(
        r"\s*scope\s*:\s*(facts|style)\s*\]\s*\[?\s*(.*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).lower(), m.group(2).strip()
    return None


# 工具元信息（供注册时使用）
TOOL_NAME = "recall_material"
TOOL_DESCRIPTION = (
    "检索用户导入的参考材料，获取与当前写作内容相关的片段。"
    "格式: recall_material[关键词]；"
    "若材料已分事实/风格库，可用 recall_material[scope:facts][关键词] 查事实依据，"
    "recall_material[scope:style][关键词] 查写法风格。"
)
