# ReportWriter 项目进展与后续 TODO

> 最后更新: 2026-08-14
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

### 3.1b ✅ 真实环境验证(P1.7,2026-08-14 已完成)
真实 LLM 端到端跑通:`write('work_summary','2026年Q2研发部工作总结')` → 5 章 **2102 字**,耗时 513 秒,MD+DOCX 双导出成功(质量高,ReAct 各节一轮 `Finish[JSON]` 即完成,Reflection 各节"无需改进")。
- 关键修复(`.env` 两处):
  1. `LLM_BASE_URL` 必须带 **`/v1`** 后缀(`http://aiserver.hisi.huawei.com/v1`),否则裸域名返回网关前端 HTML 页面,openai 库收到 str 而非 ChatCompletion
  2. 必须配 **`NO_PROXY=localhost,127.0.0.1,.huawei.com`**:公司系统代理(proxyza.huawei.com:8080)会拦截内网 LLM 域名(HIS Proxy Notification 拦截页),openai/httpx `trust_env=True` 读到 NO_PROXY 后直连即可
- `python-docx` 已在环境就绪(notebook 内核),DOCX 导出验证通过
- ⚠️ 注意:notebook 内核与 shell `python` 的 site-packages 可能不同;缺包时在内核里 `%pip install` 而非只装到 shell 环境

### 3.1c ✅ 参考材料分角色:facts/style 双库(2026-08-14 已完成)
用户场景:导入的材料有两种角色——**事实材料**(与工作内容相关,生成内容必须基于它)与**风格材料**(与工作无关,只参考写法)。已实现:
- **目录约定**: `data/facts/`(事实依据)+ `data/style/`(风格参考);任一目录存在即启用分库模式,都不存在走旧的单库模式(向后兼容)
- **Spec**: `DocumentTypeSpec` 新增 `material_facts_dir="facts"` / `material_style_dir="style"` 字段
- **MaterialManager**: `ingest(path, scope=)` / `get_relevant(query, scope=)` 支持角色,内部为每个 scope 维护独立后端实例(namespace 派生 `{ns}_{scope}`)
- **prompt 拆分**: 新增双区块任务模板(事实依据"必须基于、以此为准" / 风格参考"仅借鉴写法、严禁照搬");单库模式自动回退单区块模板
- **recall_material**: 支持 `recall_material[scope:facts][关键词]` / `[scope:style][关键词]` 定向检索
- **本地后端兜底**: 字面/2-gram 检索无命中时返回最新片段,保证"有参考"
- 冒烟测试:双库注入 5/5 章节(facts/style 各自命中)、单库模式兼容、scope 解析正确

### 3.1d ✅ 用户反馈修订(P1.9,2026-08-14 已完成)
用户场景:阅读成稿后有修改意见,需要**不重新跑全篇、只增量修订**的反馈通道。决策(已与用户确认):
- **粒度**: Jupyter 只做全文统一反馈(一段意见逐章应用);`RevisionFeedback.section_feedback` 为 WebUI 按章节细粒度反馈**预留**(dict 入参会 fail-fast 提示,不静默忽略)
- **方式**: 代码调用 `draft = orchestrator.revise(draft, feedback=...)`,不做 `input()` 交互
- **机制**: ReviewAgent + 事实材料检索——`revise_with_feedback()` 单次 LLM 调用(真实端点每章 1-2 分钟,不做多轮);修订前按 `章节标题+意见` 从 facts 库检索片段注入 prompt,保证数据/口径有据可依
- **reingest**: 材料库是内存态,fresh session(重启内核)直接修订需 `reingest=True`(默认)重新导入;同会话多轮传 `False` 省时
- 实现要点:
  - `prompts.py` 新增 `REVIEWER_REVISE_PROMPT`,并入 `get_reviewer_prompts()` 的 `"revise"` 键 → **所有 spec 零改动获得能力**;旧 spec 缺键时 Reviewer 回退模块级默认模板
  - `orchestrator.write()` 的材料导入逻辑抽为 `_ingest_materials()`,`write()`/`revise()` 共用
  - 修订记录:章节 `metadata["revision_history"]`(意见/前后字数)+ `draft.meta["revision_rounds"]`;修订稿导出到**新时间戳目录**不覆盖原稿
- 冒烟测试(stub LLM)9 项断言组通过:逐章单次调用/facts 注入/轮次记录/多轮/dict 报错/空意见报错/未知类型报错/对象入参
- **顺带修复 RAG 真实 bug**: 环境的 qdrant-client 已移除 `client.search()`,导致 `RAGBackend.search()` 的 `except: return ""` 静默吞错、RAG 检索一直返回空(此前"主动注入空、recall 工具空"均由此)。已改用新旧版通用的 `query_points()`,检索恢复命中(冒烟中 facts 片段成功注入 prompt)

### 3.1e ✅ Web UI(P1.10,2026-08-14 已完成)
- **技术栈**: FastAPI + SSE(进度推送)+ 原生 HTML/JS/CSS 单页(无构建、无 CDN 依赖);`python run_web.py` → `http://127.0.0.1:8000`
- **交互覆盖**: 撰写全流程(选类型+主题→后台跑→进度→成稿预览/下载)、**大纲确认**(规划后暂停,可编辑标题/要点再撰写)、**反馈修订**(全局+按章节细粒度——P1.9 预留的 `RevisionFeedback.section_feedback` 正式启用)、**材料上传**(facts/style,扩展名白名单复用 `loader.SUPPORTED_EXTENSIONS`,单文件 10MB 上限)
- **编排器改造**(notebook 行为不变): `write()` 拆为 `prepare()`(导入+规划)/`draft_with_outline()`(撰写+评审+导出) + 薄封装;新增 `_emit()` 进度回调(`progress_cb` 可选参数,None 时仅打印);`revise()` 支持按章节跳过(无意见章节 `continue`);导出 paths 存入 `draft.meta["export_paths"]` 供 web 下载
- **models.py**: `feedback_for` 启用按 key→title 匹配,可返回 ""(由编排器决定跳过),新增 `is_empty()`(全空才在入口报错)
- **web/ 布局**: `web/server.py`(8 个 /api 端点 + 静态挂载)、`web/session.py`(内存会话 + 事件 backlog 回放 + 15s 心跳)、`web/static/`(index.html/app.js/style.css + `vendor/mini-md.js` 手写 120 行 Markdown 渲染器——公司内网屏蔽 CDN,不用 marked.js)
- **并发与安全**: 单共享编排器 + 模块级 WRITE_LOCK(阶段线程内非阻塞获取,忙时新会话收 error 事件);大纲 key 严格校验(防改名静默丢摘要);下载路径服务端解析(防穿越);绑定 127.0.0.1
- **幂等导入**: `local_backend.ingest_file` 跳过已导入路径(web 反复 reingest 同一目录不会重复分块);RAG point ID 本就 uuid5 幂等
- **冒烟测试**(`_smoke_web.py`,agent 方法级 stub)11 项断言组通过;`_smoke_revise.py` 断言⑥同步更新(dict 反馈从"应报错"改为"合法且只改目标章")并回归通过
- **踩坑记录**: ① TestClient 经 httpx `trust_env` 会读系统代理把 `http://testserver` 请求拦到 proxyza → 测试里 patch `httpx.Client.__init__` 强制 `trust_env=False`;② TestClient 的 SSE 流式响应在连接复用下不稳定 → 冒烟直接断言 `session.events` backlog 结构/seq 单调,浏览器 EventSource 路径留给手动验证;③ 锁获取/释放必须在后台阶段线程内(不能在有路由里获取后跨 await 释放)

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
- [ ] 多轮交互式撰写(~~全局反馈~~ P1.9、~~按章节细粒度~~ P1.10 已落地;对话式交互待 WebUI 聊天形态)
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
- [x] **P1.7 真实环境验证**: 修复 .env(/v1 后缀 + NO_PROXY 绕过公司代理),真实 LLM 全链路跑通,5 章 2102 字 + MD/DOCX 导出(2026-08-14)
- [x] **P1.8 参考材料分角色**: facts/style 双库目录 + prompt 双区块注入 + recall_material scope 参数(2026-08-14)
- [x] **P1.9 用户反馈修订**: `orchestrator.revise(draft, feedback)` 全文统一意见逐章修订 + facts 检索注入 + reingest;修复 qdrant-client `search()` 已移除导致 RAG 静默返回空(2026-08-14)
- [x] **P1.10 Web UI**: FastAPI+SSE+原生前端;撰写/大纲确认/反馈修订(按章节)/材料上传;编排器拆分 prepare/draft_with_outline + 进度回调;WRITE_LOCK 串行化(2026-08-14)
- [ ] P2: 补汇报、KPI 计划类型 Spec
- [ ] P3: 交互式撰写 / 工具扩展 / 评审打分落地
