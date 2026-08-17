"""汇报 材料类型规格

与工作总结的区别：汇报是"对上/对外陈述"，强调结论先行、重点突出、
篇幅紧凑，读者时间有限；不像总结那样全面复盘，而是挑最有价值的说。
"""

from ..models import DocumentTypeSpec, SectionSpec
from ..prompts import (
    get_planner_prompts,
    get_drafter_react_prompt,
    get_drafter_task_template,
    get_reviewer_prompts,
)

SYSTEM_PROMPT = """你是一位资深的汇报材料撰写专家，擅长把复杂工作浓缩为结论先行、
重点突出、条理清晰的汇报。你写的汇报开门见山、数据说话、详略得当，
让忙碌的读者在最短时间抓住核心结论与诉求，语言精练、正式、有说服力。"""

# 汇报的章节骨架（结论先行 → 重点进展 → 数据支撑 → 问题与诉求 → 下一步）
SECTIONS = [
    SectionSpec(
        key="summary",
        title="核心结论",
        required=True,
        target_words=200,
        hints="结论先行：用 2-3 句话概括本期/本次汇报最核心的成果或判断，"
        "让读者不往下看也知道结论。",
    ),
    SectionSpec(
        key="progress",
        title="重点进展",
        required=True,
        target_words=600,
        hints="围绕结论展开 2-4 项重点进展，每项一句话说清'做成了什么+价值'，"
        "用关键数据支撑，不罗列过程细节。",
    ),
    SectionSpec(
        key="metrics",
        title="关键数据",
        required=True,
        target_words=300,
        hints="集中呈现最有说服力的 3-6 个指标（完成率/增长率/质量/效率），"
        "可用列表或简表，口径与事实材料一致。",
    ),
    SectionSpec(
        key="issues",
        title="问题与诉求",
        required=True,
        target_words=300,
        hints="客观说明当前最重要的 1-3 个问题/风险，以及需要上级或协作方"
        "给予的决策、资源或支持（汇报的落脚点）。",
    ),
    SectionSpec(
        key="next_steps",
        title="下一步安排",
        required=True,
        target_words=200,
        hints="紧扣问题与目标，给出下一步的 2-3 项关键动作与时间节点，形成闭环。",
    ),
]

ReportSpec = DocumentTypeSpec(
    type_id="report",
    material_base_dir="report",  # 材料按类型隔离: data/report/facts|style
    name="汇报",
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
    word_count_total=1600,
    tools=["recall_material"],
    review_enabled=True,
    output_formats=["markdown", "docx"],
    material_mode="rag",
    material_top_k=3,
    material_role_hint=(
        "参考事实材料中的数据与口径（必须以此为准），"
        "参考风格材料的汇报写法（结论先行、详略得当），但内容必须基于本次实际工作。"
    ),
)
