"""工作总结 材料类型规格"""

from ..models import DocumentTypeSpec, SectionSpec
from ..prompts import (
    get_planner_prompts,
    get_drafter_react_prompt,
    get_drafter_task_template,
    get_reviewer_prompts,
)

SYSTEM_PROMPT = """你是一位资深的工作总结撰写专家，擅长将零散的工作素材梳理为结构清晰、
数据翔实、重点突出的工作总结。你撰写的总结既客观陈述事实，又提炼价值与亮点，
语言专业、简洁、得体。"""

# 工作总结的章节骨架（可按需调整）
SECTIONS = [
    SectionSpec(
        key="overview",
        title="总体概述",
        required=True,
        target_words=300,
        hints="概述本期工作背景、整体进展与核心定位，给读者一个全局认识。",
    ),
    SectionSpec(
        key="highlights",
        title="重点工作与亮点",
        required=True,
        target_words=600,
        hints="提炼本期核心成果、关键突破与高价值产出，用数据与事实支撑。",
    ),
    SectionSpec(
        key="details",
        title="具体工作内容",
        required=True,
        target_words=800,
        hints="按项目/模块/职责分项展开，说明做了什么、怎么做、达成了什么。",
    ),
    SectionSpec(
        key="challenges",
        title="问题与不足",
        required=True,
        target_words=300,
        hints="客观陈述遇到的问题、不足及已采取的应对措施，体现复盘思考。",
    ),
    SectionSpec(
        key="next_plan",
        title="下期计划",
        required=True,
        target_words=300,
        hints="基于本期复盘，提出下期目标、重点工作与改进方向。",
    ),
]

WorkSummarySpec = DocumentTypeSpec(
    type_id="work_summary",
    name="工作总结",
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
    word_count_total=2300,
    tools=["recall_material"],
    review_enabled=True,
    output_formats=["markdown", "docx"],
    material_mode="rag",
    material_top_k=3,
    material_role_hint=(
        "参考历史总结的行文风格、章节结构与数据口径，"
        "但必须基于本期实际工作重写，不得直接照搬参考材料内容。"
    ),
)
