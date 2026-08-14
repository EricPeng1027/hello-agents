"""KPI 计划 材料类型规格

KPI 计划是"面向目标的承诺"：目标可衡量、可达成、有时限，
并配套行动与所需资源。写作上强调量化、对齐（与上级/部门目标挂钩）、
可考核（每个目标都说得清怎么算达成）。
"""

from ..models import DocumentTypeSpec, SectionSpec
from ..prompts import (
    get_planner_prompts,
    get_drafter_react_prompt,
    get_drafter_task_template,
    get_reviewer_prompts,
)

SYSTEM_PROMPT = """你是一位资深的 KPI 计划制定专家，擅长把战略方向拆解为可衡量、
可达成、有时限的量化目标，并配套关键行动与资源保障。你制定的计划对齐上级目标、
SMART 可考核、有节奏有抓手，语言精确、务实、不空谈。"""

# KPI 计划的章节骨架（目标总览 → 量化指标 → 关键行动 → 资源保障 → 考核节奏）
SECTIONS = [
    SectionSpec(
        key="objectives",
        title="目标总览",
        required=True,
        target_words=300,
        hints="本期总体目标与导向：对齐公司/部门战略，说明本期聚焦的 1-2 个主战场"
        "与总体预期（可用一句话目标 + 总体量化期望）。",
    ),
    SectionSpec(
        key="indicators",
        title="量化指标",
        required=True,
        target_words=600,
        hints="核心章节：3-6 项 KPI 指标，每项给出 指标名/当前基线/目标值/口径"
        "（怎么算达成），尽量用表格或结构化列表，数据与事实材料一致。",
    ),
    SectionSpec(
        key="actions",
        title="关键行动",
        required=True,
        target_words=500,
        hints="为达成指标的关键举措：每项行动说明 做什么/负责人角色/时间节点/"
        "预期贡献到哪个指标，行动与指标一一呼应。",
    ),
    SectionSpec(
        key="resources",
        title="资源与保障",
        required=True,
        target_words=300,
        hints="达成目标所需的资源（人力/预算/协作方/工具）与机制保障"
        "（例会/评审/风险预案），以及关键依赖。",
    ),
    SectionSpec(
        key="review_cadence",
        title="考核与复盘节奏",
        required=True,
        target_words=200,
        hints="考核口径与复盘机制：按什么周期检查（周/月/季）、由谁评估、"
        "未达预期的纠偏方式。",
    ),
]

KpiPlanSpec = DocumentTypeSpec(
    type_id="kpi_plan",
    name="KPI 计划",
    paradigm="plan_solve",
    system_prompt=SYSTEM_PROMPT,
    custom_prompts={
        # Planner 使用 PlanAndSolve 范式
        "planner": get_planner_prompts()["planner"],
        "executor": get_planner_prompts()["executor"],
        # Drafter 使用 ReAct 范式
        "drafter_react": get_drafter_react_prompt(),
        "drafter_task": get_drafter_task_template(),
        # Reviewer 使用 Reflection 范式
        "reviewer": get_reviewer_prompts(),
    },
    sections=SECTIONS,
    word_count_total=1900,
    tools=["recall_material"],
    review_enabled=True,
    output_formats=["markdown", "docx"],
    material_mode="rag",
    material_top_k=3,
    material_role_hint=(
        "指标基线与口径必须以事实材料为准；风格材料仅参考计划的结构化写法"
        "（量化、对齐、可考核），不得照搬其指标数值。"
    ),
)
