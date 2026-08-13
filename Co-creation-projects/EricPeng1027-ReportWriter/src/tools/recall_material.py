"""recall_material 检索工具

作为 DraftingAgent 的本地工具，在 ReAct 撰写过程中可被调用，
从用户导入的参考材料库中检索相关片段（风格/口径/事实参考）。

Drafter 的 ToolExecutor 接受 (name, description, func(str)->str)，
故这里提供一个工厂函数生成可调用对象。
"""

from typing import Callable

from ..materials.manager import MaterialManager


def build_recall_material_tool(
    material_manager: MaterialManager, top_k: int = 3
) -> Callable[[str], str]:
    """构建 recall_material 工具函数

    Args:
        material_manager: 材料管理器
        top_k: 每次召回的片段数

    Returns:
        一个 (query: str) -> str 的可调用函数
    """

    def recall_material(query: str) -> str:
        result = material_manager.search(query, top_k=top_k)
        return result or "（未检索到相关参考材料）"

    return recall_material


# 工具元信息（供注册时使用）
TOOL_NAME = "recall_material"
TOOL_DESCRIPTION = (
    "检索用户导入的参考材料（范文/历史总结等），获取与当前写作内容相关的片段，"
    "用于借鉴行文风格、结构口径与事实依据。格式: recall_material[检索关键词]"
)
