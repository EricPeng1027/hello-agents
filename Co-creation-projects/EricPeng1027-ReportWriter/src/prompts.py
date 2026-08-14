"""提示词模板

集中管理各范式的通用提示词。具体材料类型可在其 Spec 的 custom_prompts 中覆盖。
所有提示词使用 .format() 占位符，占位符在 Agent 包装层填充。
"""

# =============================================================================
# Planner（PlanAndSolve 范式）：生成章节大纲
# =============================================================================

PLANNER_PROMPT = """你是一位资深的材料撰写规划专家。请将以下撰写任务分解为清晰的规划步骤。

撰写任务:
{question}

请按以下格式输出规划步骤:
```python
[
    "步骤1: 分析主题与目标读者，明确材料定位",
    "步骤2: 梳理整体结构与逻辑主线",
    "步骤3: 为每个章节设定要点与重点内容",
    "步骤4: 组装完整的章节大纲"
]
```
不能超过 8 个步骤。
"""

PLANNER_EXECUTOR_PROMPT = """你是材料撰写规划执行专家。请按照规划步骤执行章节大纲的生成。

# 原始任务: {question}
# 规划步骤: {plan}
# 已完成步骤: {history}
# 当前步骤: {current_step}

▸ 关键要求：
- 不能超过 8 个步骤。
- 如果当前步骤包含"组装"、"完整"、"大纲"等关键词，**必须**输出完整的 JSON 格式章节大纲。
- 如果不是最后一步，请输出当前步骤的分析结果（文本格式）。

**最后一步的输出格式（必须是 JSON，不要添加任何其他文本）**：
```json
{{
  "title": "材料总标题",
  "sections": [
    {{
      "key": "章节标识",
      "title": "章节标题",
      "summary": "本章主要内容概述（30-80字）",
      "key_points": ["要点1", "要点2", "要点3"]
    }}
  ]
}}
```

**重要**：如果是最后一步，请直接输出 JSON，不要添加前缀文本。

请执行当前步骤：
"""

# =============================================================================
# Drafter（ReAct 范式）：逐节撰写
# =============================================================================

DRAFTER_REACT_PROMPT = """你是一位专业的材料撰写专家，擅长撰写高质量的工作总结、汇报、计划等材料。

## 可用工具
{tools}

## 工作流程
请严格按照以下格式回应，每次只能执行一个步骤：

Thought: 分析当前章节的写作需求，判断是否需要检索参考材料获取事实或风格。
Action: 选择下一步行动，格式为：
- `{{tool_name}}[{{tool_input}}]`：调用工具检索参考材料。
- `Finish[JSON内容]`：当你完成章节撰写时，输出最终结果。

## 当前写作任务
{question}

## 执行历史
{history}

现在开始你的推理和撰写："""

DRAFTER_TASK_TEMPLATE = """请撰写材料中的某一章节。

## 章节信息
- 章节标题: {section_title}
- 章节要求: {section_hints}
- 目标字数: {target_words} 字（允许误差 ±10%）

## 大纲要点
{outline_summary}

## 事实依据（内容必须基于以下材料，数据/事实/口径以此为准）
{facts_materials}

## 风格参考（仅借鉴行文风格、结构与表达方式，严禁照搬其内容与数据）
{style_materials}

## 输出要求
完成撰写后，必须使用 `Finish[JSON内容]` 格式输出结果，JSON 结构如下：
```json
{{
  "key": "{section_key}",
  "title": "{section_title}",
  "content": "章节正文（Markdown格式）",
  "word_count": 实际字数
}}
```

重要：
- content 字段必须包含完整的章节正文。
- 事实依据是内容的事实来源，必须基于它撰写；风格参考仅用于学习写法，不得把其中的事实/数据写入正文。
- 如需补充事实或确认口径，可调用 recall_material 工具检索（facts 查事实，style 查写法）。
"""

# 单库模式（未分 facts/style 目录时）使用的任务模板，保持与旧版兼容
DRAFTER_TASK_TEMPLATE_SINGLE = """请撰写材料中的某一章节。

## 章节信息
- 章节标题: {section_title}
- 章节要求: {section_hints}
- 目标字数: {target_words} 字（允许误差 ±10%）

## 大纲要点
{outline_summary}

## 参考材料（仅供参考风格、口径与事实，须基于当前主题重写，不得照搬）
{reference_materials}

## 输出要求
完成撰写后，必须使用 `Finish[JSON内容]` 格式输出结果，JSON 结构如下：
```json
{{
  "key": "{section_key}",
  "title": "{section_title}",
  "content": "章节正文（Markdown格式）",
  "word_count": 实际字数
}}
```

重要：
- content 字段必须包含完整的章节正文。
- 参考材料仅用于借鉴行文风格、结构与数据口径，必须基于当前实际主题重新撰写。
- 如需补充事实或确认口径，可调用 recall_material 工具检索参考材料。
"""

# =============================================================================
# Reviewer（Reflection 范式）：自评与修订
# =============================================================================

REVIEWER_PROMPTS = {
    "initial": """你是一位严格的材料评审专家。请对以下材料章节进行评审与优化。

# 任务: 评审并优化章节内容
# 章节内容:
{task}

请输出优化后的完整内容（Markdown格式），不要输出评审过程，直接给出优化后的正文。
""",
    "reflect": """你是一位严格的材料评审专家。请评审以下章节内容：

# 写作任务: 评审章节质量
# 当前内容:
{content}

请从以下维度评审：
1. **内容质量** (40分): 准确性、完整性、深度、贴合主题
2. **结构逻辑** (30分): 层次清晰、逻辑连贯、过渡自然
3. **语言表达** (20分): 简洁专业、用词准确、语气得体
4. **格式规范** (10分): 字数达标、格式正确、排版美观

如果内容质量很好（85分以上），请回答"无需改进"。
否则，请详细指出问题并提供具体的修改建议。
""",
    "refine": """请根据评审意见优化章节内容：

# 当前内容:
{last_attempt}

# 评审意见:
{feedback}

请输出优化后的完整章节正文（Markdown格式），不要输出解释。
""",
}


# 用户反馈修订模板：用户阅读成稿后提出修改意见，Reviewer 按意见增量修订。
# 与 reflect/refine 的区别：feedback 来自用户而非自评，且注入事实材料保证数据口径。
REVIEWER_REVISE_PROMPT = """你是一位材料修订专家。用户审阅了以下章节并提出了修改意见，请严格落实。

# 章节标题: {section_title}
# 章节当前内容:
{content}

# 用户修改意见（适用于全文，请落实与本章相关的部分）:
{feedback}

# 事实依据（修订涉及的数据/事实/口径必须以此为准；无相关内容时保持原文数据不变）:
{facts_materials}

## 修订要求
- 严格落实用户意见中与本章相关的部分；与本章无关的意见不要强行套用
- 未提及的部分保持原样，不要重写全文
- 数据/事实/口径必须与上方事实依据一致；事实依据中没有的数据不得编造
- 保持原目标字数: {target_words} 字左右（±10%）
- 只输出修订后的完整章节正文（Markdown格式），不要输出解释或修订说明
"""


def get_planner_prompts() -> dict:
    """获取 Planner 的 custom_prompts"""
    return {"planner": PLANNER_PROMPT, "executor": PLANNER_EXECUTOR_PROMPT}


def get_drafter_react_prompt() -> str:
    """获取 Drafter 的 ReAct 自定义提示词"""
    return DRAFTER_REACT_PROMPT


def get_drafter_task_template() -> str:
    """获取 Drafter 的章节任务模板（双区块：事实依据 + 风格参考）"""
    return DRAFTER_TASK_TEMPLATE


def get_drafter_task_template_single() -> str:
    """获取 Drafter 的章节任务模板（单库模式，向后兼容）"""
    return DRAFTER_TASK_TEMPLATE_SINGLE


def get_reviewer_prompts() -> dict:
    """获取 Reviewer 的 Reflection 自定义提示词

    在自评三件套（initial/reflect/refine）之外并入 "revise"（用户反馈修订），
    所有使用该 getter 的 spec 自动获得用户修订能力，无需改动 types 文件。
    """
    prompts = dict(REVIEWER_PROMPTS)
    prompts["revise"] = REVIEWER_REVISE_PROMPT
    return prompts
