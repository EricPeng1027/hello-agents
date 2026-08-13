# ReportWriter —— 智能材料撰写 Agent

> 基于 HelloAgents v1.0.0 框架，撰写工作总结、汇报、KPI 计划等多类型材料，支持导入参考材料并扩展更多材料类型。

## 📝 项目简介

ReportWriter 是一个 AI 原生的材料撰写智能体。它把"撰写一份材料"拆解为 **规划 → 撰写 → 评审** 三段式流水线，分别由三种 HelloAgents 经典范式驱动：

- **PlannerAgent**（Plan-and-Solve）：把主题分解为步骤，产出对齐章节骨架的 JSON 大纲
- **DraftingAgent**（ReAct）：逐节撰写，可调用工具检索参考材料
- **ReviewAgent**（Reflection）：对内容自评并迭代优化

核心特色：
1. **多类型可扩展**——新增一类材料 ≈ 新增一个 `DocumentTypeSpec` 配置，零侵入核心代码
2. **参考材料导入**——用户可导入写好的范文/历史材料，Agent 撰写时参考其风格与口径（RAG）
3. **双格式导出**——Markdown + DOCX

## 🏗️ 架构

```
用户输入 (类型+主题+参考材料目录)
   │
   1. MaterialManager.ingest()        导入参考材料（RAG 优先，未配置自动降级本地后端）
   2. PlannerAgent.plan()             PlanAndSolve 出章节大纲
   3. for section: DraftingAgent.draft()   ReAct 逐节撰写
        ├─ 主动注入：按章节检索参考片段拼进 prompt
        └─ 被动召回：recall_material 工具按需检索
   4. ReviewAgent.review()            Reflection 自评修订
   5. Exporter.export()               导出 Markdown + DOCX
```

```
src/
├── config.py            配置（.env 统一变量）
├── models.py            DocumentTypeSpec / SectionSpec / DocumentDraft
├── registry.py          材料类型注册表（扩展核心）
├── prompts.py           通用提示词模板
├── utils.py             JSON 提取/字数统计
├── exporter.py          Markdown + DOCX 导出
├── orchestrator.py      流水线编排
├── agents/
│   ├── llm_service.py   HelloAgentsLLM 单例
│   ├── planner.py       PlanAndSolve 包装
│   ├── drafter.py       ReAct 包装
│   └── reviewer.py      Reflection 包装
├── materials/
│   ├── manager.py       MaterialManager（导入/检索统一入口）
│   ├── rag_backend.py   Qdrant + Embedding RAG 后端
│   ├── local_backend.py 本地内存后端（RAG 不可用时兜底）
│   └── loader.py        目录扫描
├── tools/
│   └── recall_material.py   检索参考材料工具
└── types/
    └── work_summary.py      工作总结 Spec
```

## ✨ 核心功能

- [x] 工作总结类型端到端撰写（P1）
- [x] 参考材料导入与检索：RAG（Qdrant+Embedding）+ 本地兜底双后端，主动注入 + 被动召回
- [x] Markdown + DOCX 双格式导出
- [x] 可扩展类型注册表
- [ ] 汇报、KPI 计划等类型（P2）

## 🛠️ 技术栈

- **框架**：HelloAgents v1.0.0（HelloAgentsLLM / SimpleAgent 为基础，三种范式用教程原生文本写法实现）
- **范式**：Plan-and-Solve + ReAct + Reflection（Thought/Action/Observation 文本循环，不依赖 Function Calling）
- **向量库**：Qdrant + OpenAI 兼容 Embedding（RAG 参考材料，可选）
- **本地兜底**：RAG 未配置时自动降级为本地内存检索（纯字面/2-gram 匹配，零外部依赖）
- **文档**：python-docx（DOCX 导出）

## 🚀 快速开始

### 环境要求

- Python 3.10+
- （可选）Qdrant 实例 + Embedding 服务：用于 RAG 参考材料；不配也能跑，自动用本地兜底模式

### 安装依赖

```bash
pip install -r requirement.txt
```

### 配置

编辑 `.env`，填写以下配置：

```bash
# LLM（框架统一变量）
LLM_MODEL_ID=your-model
LLM_API_KEY=your-key
LLM_BASE_URL=https://your-llm-endpoint

# （可选）Qdrant 向量库（RAG 参考材料；不配则自动降级为本地模式）
QDRANT_URL=https://your-cluster.qdrant.tech:6333
QDRANT_API_KEY=your_qdrant_key

# （可选）Embedding
EMBED_MODEL_TYPE=dashscope
EMBED_API_KEY=your_embed_key
```

### 放入参考材料

把写好的范文/历史总结放入 `data/` 目录（支持 md/docx/pdf/txt/html/json）：

```
data/
├── 去年Q1工作总结.docx
├── 优秀汇报范文.md
└── ...
```

### 运行

```bash
jupyter lab
# 打开 main.ipynb 顺序运行
```

或用脚本：

```python
from src.orchestrator import ReportWriterOrchestrator

orch = ReportWriterOrchestrator()
draft = orch.write(
    type_id="work_summary",
    topic="2026年Q2 研发部工作总结",
)
# 结果输出到 outputs/<时间戳>/ 下的 .md 和 .docx
```

## 📖 扩展：新增一种材料类型

新增"述职报告"为例，零侵入核心代码：

1. 在 `src/types/` 新建 `debriefing.py`，定义一个 `DocumentTypeSpec`：

```python
from ..models import DocumentTypeSpec, SectionSpec

DebriefingSpec = DocumentTypeSpec(
    type_id="debriefing",
    name="述职报告",
    paradigm="plan_solve",
    sections=[
        SectionSpec("duties", "岗位职责", True, 300, "..."),
        SectionSpec("achievements", "主要业绩", True, 800, "..."),
        # ...
    ],
    material_mode="rag",
    # ...
)
```

2. 在 `src/registry.py` 的 `_register_builtin_types` 注册：

```python
from .types.debriefing import DebriefingSpec
registry.register(DebriefingSpec)
```

3. 如需专属能力，在 `src/tools/` 加一个 `Tool` 子类并在 Spec 的 `tools` 引用。

核心编排、Agent、导出逻辑完全不动。

## 📊 性能评估

- 总字数、各章节字数、耗时、参考材料命中片段数均记录在 `meta.json` 与文档附录中

## 🔮 未来计划

- [ ] P2：补充汇报、KPI 计划类型
- [ ] 数据表读取/图表生成工具，支撑数据型材料
- [ ] 多轮交互式撰写（用户反馈后增量修订）

## 🤝 贡献指南

欢迎提出 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 🙏 致谢

感谢 Datawhale 社区和 Hello-Agents 项目！
