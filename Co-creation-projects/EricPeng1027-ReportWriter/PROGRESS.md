# ReportWriter 项目进展与后续 TODO

> 最后更新: 2026-08-13
> 分支: `feature/EricPeng1027-ReportWriter`
> 位置: `Co-creation-projects/EricPeng1027-ReportWriter/`

---

## 一、项目定位

基于 **HelloAgents v1.0.0** 框架的多类型材料撰写智能体。支持撰写工作总结、汇报、KPI 计划等,可导入参考材料(RAG)参考其风格与口径,输出 Markdown + DOCX,且类型可扩展。

---

## 二、已完成工作(P1 ✅)

### 2.1 决策(已与用户确认)
- P1 首跑类型:**工作总结**
- 输出格式:**Markdown + DOCX**
- 参考材料后端:**RAG**(Qdrant + Embedding)

### 2.2 已交付文件
```
EricPeng1027-ReportWriter/
├── main.ipynb                  # 7段式入口(已同步真实 API)
├── README.md                   # 完整文档
├── requirement.txt             # 含 hello-agents/python-docx/qdrant-client/openai/markitdown
├── .env                        # 已配 LLM_*;QDRANT_*/EMBED_* 仍为占位符
├── data/                       # 放参考材料(md/docx/pdf/txt)
├── outputs/                    # 生成结果
└── src/
    ├── __init__.py
    ├── config.py               # Settings(pydantic)
    ├── models.py               # DocumentTypeSpec/SectionSpec/DocumentDraft/ReviewResult
    ├── registry.py             # 类型注册表(扩展核心)
    ├── prompts.py              # 三范式提示词模板
    ├── utils.py                # JSONExtractor/字数统计/时间戳
    ├── exporter.py             # Markdown + DOCX 导出
    ├── orchestrator.py         # 流水线编排
    ├── agents/
    │   ├── __init__.py
    │   ├── llm_service.py      # HelloAgentsLLM 单例
    │   ├── planner.py          # Plan-and-Solve(教程原生写法)
    │   ├── drafter.py          # ReAct(教程原生)+ recall_material 工具
    │   └── reviewer.py         # Reflection(教程原生)
    ├── materials/
    │   ├── __init__.py
    │   ├── manager.py          # MaterialManager(auto/rag/local 三模式)
    │   ├── rag_backend.py      # Qdrant+Embedding 自建 RAG
    │   ├── local_backend.py    # 本地内存兜底(纯字面+2-gram 匹配)
    │   └── loader.py           # 目录扫描
    ├── tools/
    │   ├── __init__.py
    │   └── recall_material.py  # 检索参考材料工具(函数式)
    └── types/
        ├── __init__.py
        └── work_summary.py     # 工作总结 Spec(P1)
```

### 2.3 关键架构决策(实现中对齐了框架真实 API)

1. **Agent 范式用"教程原生写法",非框架 Function Calling 类**
   - 框架 1.0.0 的 `PlanSolveAgent`/`ReActAgent`/`ReflectionAgent` 强依赖 `invoke_with_tools`(Function Calling),用户的华为 LLM 端点未必支持。
   - 改为参考 `code/chapter4` 的原生范式:Thought/Action/Observation 文本循环 + `Finish[...]`,底层只用 `HelloAgentsLLM.invoke()`,对任意 OpenAI 兼容端点都稳。

2. **RAGTool 不存在于框架 1.0.0**
   - `hello_agents.tools` 只导出 `Tool/ToolParameter/ToolRegistry/CalculatorTool` 等,**无 RAGTool**(仅在教程 vendored 副本里)。
   - 在 `src/materials/rag_backend.py` 自建轻量 RAG:`qdrant-client` + OpenAI 兼容 Embedding + `markitdown` 解析多格式。

3. **Tool 体系改为本地下发**
   - 框架 `Tool` 抽象基类要求返回 `ToolResponse`,与原生 ReAct 文本循环不匹配。
   - Drafter 内置轻量 `_ToolExecutor`(name→func(str)→str),`recall_material` 以函数注册,符合第四章 `tool[input]` 约定。

4. **参考材料双模式**
   - 主动注入:编排器每节撰写前按章节检索 Top-K 片段拼进 prompt(必定可见)
   - 被动召回:Drafter 在 ReAct 循环中可调 `recall_material[关键词]` 按需细查

5. **容错兜底**
   - Planner JSON 解析失败 → 回退 Spec 章节骨架
   - Drafter ReAct 失败/内容过短 → 回退 SimpleAgent 重写
   - Reviewer 失败 → 保留原文
   - RAG 未就绪 → 降级跳过参考材料,仍可生成

### 2.4 框架源码位置(已核对的真实 API)
- 安装位置: `C:\Users\p30014306\AppData\Local\Programs\Python\Python312\Lib\site-packages\hello_agents\`
- 版本: `1.0.0`
- 顶层导出: `HelloAgentsLLM, SimpleAgent, ReActAgent, ReflectionAgent, PlanSolveAgent, ToolRegistry, Config, Message, ...`
- `HelloAgentsLLM.invoke(messages) -> LLMResponse`(`.content` 取文本),`think()`/`stream_invoke()` 流式
- ⚠️ `hello_agents.tools` **不导出** RAGTool/MemoryTool/SearchTool/MCPTool

---

## 三、未完成 / 待办(P2 及以后)

### 3.1 ✅ 运行验证(P1.6,2026-08-13 已完成)
冒烟测试已全部通过:
- 核心模块导入 ✅(`pydantic-settings` 缺失已补装)
- 编排器实例化 ✅(LLM 单例 OK,RAG 未装时正确降级)
- 全链路 stub 测试 ✅(规划→撰写→评审→导出,5 章 1317 字,参考材料注入 5/5)
- 发现并修复:
  1. **MaterialManager 新增 `auto`/`local` 模式 + `local_backend.py`**:RAG 不可用(未装 qdrant-client/未配 QDRANT_URL)时自动降级为本地内存检索(字面 + 2-gram 部分匹配),不再丢失参考材料能力。编排器默认 `mode="auto"`。
  2. **Drafter 回退清洗**:兜底撰写输出会剥离混入的 ReAct 痕迹(Thought/Action/Finish),避免把推理文本当正文。
  3. **不达标阈值**:撰写结果低于目标字数 40% 触发回退(原来只卡 <30 字)。
  4. **SimpleAgent trace 关闭**:回退撰写传 `Config(trace_enabled=False)`,不再在项目根目录生成 `memory/traces/`。
- ⚠️ 真实 LLM 端到端未验证:当前网络代理拦截 aiserver.hisi.huawei.com(HIS Proxy Notification),需在能访问内网 LLM 的环境跑一次真实 `write()`。
- ⚠️ `python-docx` 未装,DOCX 导出未实测(代码路径已有 try/except 兜底)。建议 `pip install python-docx qdrant-client markitdown` 补齐。

### 3.2 🟡 环境配置补全(真实 RAG 运行前必做)
用户 `.env` 里以下两组还是占位符:
```bash
# Qdrant(本地 Docker 最快: docker run -p 6333:6333 qdrant/qdrant)
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=

# Embedding(dashscope text-embedding-v3,1024维,config.py 已对齐)
EMBED_MODEL_TYPE=dashscope
EMBED_MODEL_NAME=text-embedding-v3
EMBED_API_KEY=<你的dashscope_key>
EMBED_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```
> RAG 未就绪时已不再阻塞:自动降级为本地参考材料模式,仍可生成并注入参考片段。

### 3.3 🟡 P2:补"汇报""KPI 计划"两个类型 Spec
在 `src/types/` 新增 `report.py`、`kpi_plan.py`,定义 `DocumentTypeSpec` 并在 `registry.py` 的 `_register_builtin_types` 注册。核心代码零改动,验证可扩展性。
- KPI 类型可能需专属工具(如 `kpi_calculator`),在 `src/tools/` 加 `Tool` 子类并在 spec.tools 引用。

### 3.4 🟢 P3 及以后
- [x] ~~本地后端兜底(无 Qdrant 时用关键词/章节匹配,`material_mode="local"`)~~ —— P1.6 已实现 `local_backend.py` + `auto` 模式
- [ ] 数据表读取/图表生成工具,支撑数据型材料
- [ ] 多轮交互式撰写(用户对大纲/章节反馈后增量修订)
- [ ] Planner/Drafter 的提示词调优(尤其 ReAct 的 `Finish[JSON]` 稳定性)
- [ ] 评审打分维度落地(`ReviewResult` 目前未真正填充分数)

---

## 四、关键文件速查(继续工作时的入口)

| 想做什么 | 看哪里 |
|---|---|
| 改流水线/加步骤 | `src/orchestrator.py` |
| 加新材料类型 | `src/types/`(新建文件)+ `src/registry.py`(注册) |
| 改某范式的提示词 | `src/prompts.py` + `src/types/work_summary.py` 的 `custom_prompts` |
| 调 RAG 检索/导入 | `src/materials/rag_backend.py`、`src/materials/manager.py` |
| 调本地兜底检索 | `src/materials/local_backend.py` |
| 调导出格式 | `src/exporter.py` |
| 调配置/阈值 | `src/config.py` + `.env` |
| 框架真实 API 参考 | `Python312/Lib/site-packages/hello_agents/` |
| 教程范式参考 | `code/chapter4/`(ReAct/Plan_and_solve/Reflection.py) |
| 最相似参考项目 | `Co-creation-projects/melxy1997-ColumnWriter/` |

---

## 五、本次会话的 TODO 终态

- [x] P1.1 基础设施: config/models/registry/utils/llm_service + work_summary spec + prompts
- [x] P1.2 材料层: MaterialManager + rag_backend + recall_material 工具
- [x] P1.3 三 Agent: planner(PlanAndSolve) + drafter(ReAct) + reviewer(Reflection)
- [x] P1.4 编排+导出: orchestrator 串流水线 + exporter(MD+DOCX)
- [x] P1.5 打磨: main.ipynb 7段式 + README + 错误兜底 + 依赖更新
- [x] **P1.6 运行验证**: 冒烟测试通过 + 本地兜底后端 + 回退清洗/trace 关闭(2026-08-13)
- [ ] **P1.7 真实环境验证**: 在能访问内网 LLM 的环境跑一次真实 `write()`;补装 `python-docx/qdrant-client/markitdown` 验证 DOCX/RAG
- [ ] P2: 补汇报、KPI 计划类型 Spec
- [ ] P3: 交互式撰写 / 工具扩展 / 评审打分落地
