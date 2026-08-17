# ReportWriter 项目进展与后续 TODO

> 最后更新: 2026-08-17
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

### 2.2 已交付文件（2026-08-14 整理后）
```
EricPeng1027-ReportWriter/
├── main.ipynb                  # 7段式入口(已同步真实 API)
├── run_web.py                  # Web UI 启动入口
├── README.md                   # 完整文档
├── requirement.txt             # 依赖(hello-agents/docx/qdrant/openai/markitdown/fastapi/sse)
├── .env                        # 已配 LLM_*;QDRANT_*/EMBED_* 已可用
├── data/                       # 参考材料（按类型分目录：<type_id>/facts 事实 + style 风格）
├── outputs/                    # 生成结果(时间戳子目录)
├── src/
│   ├── config.py               # Settings(pydantic)
│   ├── models.py               # Spec/Draft/RevisionFeedback/ReviewResult
│   ├── registry.py             # 类型注册表(扩展核心)
│   ├── prompts.py              # 三范式提示词模板
│   ├── utils.py                # JSONExtractor/字数统计/时间戳
│   ├── exporter.py             # Markdown + DOCX 导出
│   ├── orchestrator.py         # 流水线编排(write/prepare/draft_with_outline/revise)
│   ├── agents/                 # llm_service + planner + drafter + reviewer
│   ├── materials/              # manager + rag_backend + local_backend + loader
│   ├── tools/                  # recall_material + read_data_table 工具
│   └── types/                  # work_summary + report + kpi_plan 三个 Spec
└── web/
    ├── server.py               # FastAPI 路由 + SSE + 后台阶段执行
    ├── session.py              # 内存会话 + 事件队列
    └── static/                 # 前端(index.html/app.js/style.css + vendor/mini-md.js)
```
> 整理说明:已删除 3 个冒烟脚本(_smoke_revise/types/web.py,验证结论保留在各 P 条目)、outputs/ 早期导出、__pycache__。

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

### 3.2 ✅ 环境配置补全（2026-08-17 已验证）
`.env` 已切换为 **Qdrant（本地 Docker）+ Ollama 本地 Embedding（qwen3-embedding:8b, 4096 维）** 的真实 RAG 配置：
- `QDRANT_URL=http://localhost:6333`（本地 Docker，`qdrant/qdrant` 容器）
- `EMBED_BASE_URL=http://localhost:11434/v1`（Ollama OpenAI 兼容端点）
- `QDRANT_VECTOR_SIZE=4096`（与 qwen3-embedding:8b 对齐）
- **验证结果**：编排器 `material_mode=rag` 生效，facts/style 各自独立 collection（`reportwriter_vectors_*_facts/style`），检索命中正常（注入示例片段成功）；旧 `dashscope` 配置已注释保留，可一键切回
- 本地 Qdrant 存留了历史 collection（reportwriter/demo/web），多 namespace 互不干扰

### 3.3 ✅ P2:补"汇报""KPI 计划"两个类型 Spec(2026-08-14 已完成)
验证"新增类型 ≈ 新增 Spec 配置、零侵入核心代码"的扩展性承诺:
- **新增** `src/types/report.py`(汇报,5 章:核心结论/重点进展/关键数据/问题与诉求/下一步安排,1600 字,结论先行)与 `src/types/kpi_plan.py`(KPI 计划,5 章:目标总览/量化指标/关键行动/资源与保障/考核与复盘节奏,1900 字,SMART 可考核)
- **注册**: `registry.py` 的 `_register_builtin_types` 加两行 import+register,核心代码(编排器/Agent/导出/web)**零改动**
- 两类型复用全部通用 prompt(planner/executor/drafter_react/drafter_task/reviewer 含 revise)与 facts/style 分库材料层;web `/api/types` 自动出现新类型,大纲确认/反馈修订等交互无需任何适配
- **冒烟测试**(`_smoke_types.py`,agent 方法级 stub)5 项断言组通过:类型注册/spec 结构(含 revise 模板)/两类型端到端 write(各 5 章)/按章节修订只改目标章/work_summary 回归
- KPI 类型暂未加专属工具(如 kpi_calculator):当前 recall_material 已够用,量化计算工具留待真实使用中按需补(P3 工具扩展一并考虑)

### 3.4b ✅ 材料按类型分目录 + 管理页面（2026-08-17 已完成）
用户反馈：参考材料此前所有类型共享 `data/facts|style`，会互相污染检索。已实现按类型隔离 + Web 管理页：
- **目录结构**：`data/<type_id>/facts|style`（如 `data/work_summary/facts/`）；Spec 新增 `material_base_dir` 字段（三个内置类型已配为各自 type_id）；历史 `data/facts|style` 文件已迁移到 `work_summary` 下
- **检索隔离**：scope 从 `facts` 升级为 `"<type_id>:facts"` 命名空间，`MaterialManager._get_backend` 惰性派生独立后端（RAG collection 形如 `reportwriter_vectors_reportwriter_web_work_summary_facts`）；`recall_material` 在 `prepare()` 后按当前类型重注册（default_scope 带类型），`read_data_table` 查找范围扩展到各类型子目录
- **管理 API**：`GET /api/materials[?type_id=]`（清单，含大小/修改时间/后端模式）、`POST /api/materials/upload`（必带 `type_id`，写入对应类型目录）、`DELETE /api/materials/{type}/{scope}/{file}`（basename 校验防穿越）、`POST /api/materials/reingest[?type_id=]`（按类型把磁盘材料导入检索库，幂等）
- **管理页面**：左侧新增「🗂 材料管理」面板（类型筛选、表格列出类型/库/文件/大小、删除按钮、「同步到检索库」按钮）；上传控件标注"上传到当前所选类型"（**2026-08-17 已改为顶部导航独立视图**，见 3.4c）
- **验证**：真实 RAG 类型专属库检索命中（`work_summary:facts` 正确召回 q2_data）；类型间隔离确认（`report:facts` 检索不到 work_summary 材料）；`_smoke_p3.py` 扩展至 **40 项断言**（新增用例6 分目录隔离 5 项 + 用例7 管理 API 10 项，含路径穿越/未知类型/重复删除边界）

### 3.4c ✅ 前端美化：顶部导航分功能区（2026-08-17 已完成）
用户反馈：功能区（撰写/材料管理）堆在左栏卡片里层次不清。已重构为**顶部导航栏**形态：
- **导航栏**：吸顶 header 内置「✍️ 撰写工作台 / 🗂 材料管理」两个 tab，右侧常驻状态徽章（从进度卡挪到 header，任何视图可见）；tab 切换带 fadeIn 过渡
- **撰写工作台视图**：保持原有左右双栏（设置/对话/进度 + 大纲/成稿），「🗂 材料管理」按钮从设置卡移除
- **材料管理视图**：独立整页（最大宽度 1000px 居中），页面头部 = 标题 + 类型筛选 + 同步按钮一排；新增**上传区**（类型 + 事实/风格 + 文件选择一体，摆脱对左栏上传控件的依赖）；材料表格美化（表头底色、行 hover、facts/style 彩色 scope 标签）
- **样式升级**：CSS 变量统一配色（--brand/--ok/--warn/--err）、按钮/卡片过渡、上传区虚线框、移动端 header 折行适配
- **验证**：40 项冒烟断言回归全过；TestClient 确认 `/`、`/style.css`、`/app.js` 均含新导航结构

### 3.5 ✅ P5：类型结构可配置 + 对话式撰写独立页面（2026-08-17 已完成）
用户两个改进点：①每种类型的章节骨架/提示词应可配置、未配置走默认；②对话式撰写不该挤在左栏小卡片。
- **YAML 覆盖层**：新增 `src/type_config.py`——`config/types/<type_id>.yaml` 只存用户覆盖字段（diff 式，不写全量默认）；可覆盖项限纯数据（`system_prompt`/`material_role_hint`/`word_count_total`/`sections` 整体替换），机制字段（paradigm/tools/material_*）不开放；`save/load/delete` + 校验（章节 key 唯一、字数正整数等，非法抛 `TypeConfigError`）
- **registry 加载/热更新**：注册表留档内置默认 `_defaults`；`get_registry()` 注册内置后 `apply_overrides_from_disk()` 套用覆盖（非法 YAML/未注册类型仅告警跳过，不影响内置）；`refresh_type(type_id)` 供 web 保存/删除后就地热更新，**编排器/Agent/web 零改动**获得覆盖后结构
- **配置 API**：`GET/PUT/DELETE /api/types/{type_id}/config`——GET 返回生效值+`is_overridden`+内置默认（编辑器对照用）；PUT 校验后写 YAML 并热更新；DELETE 删 YAML 回退默认；PUT/DELETE 走 WRITE_LOCK 与撰写互斥；`config/types/README.md` 说明格式
- **Web 类型配置编辑器**：顶部导航新增「⚙️ 类型配置」视图——角色设定/材料说明文本域 + 章节表格（标识/标题/字数/要点，增删行、上移下移）、已覆盖角标、保存/恢复默认；保存后 `loadTypes()` 刷新章节预览
- **对话式撰写独立页面**：`card-chat` 从左栏移除，新增整页视图 `view-chat`（单列居中 820px，消息区 flex 撑满、输入区贴底、页内类型选择器、choosing 时主题横幅）；撰写页「💬 对话式撰写」按钮改为切视图并预填类型/首条消息；SSE 收到 `start` 事件自动切回撰写工作台看进度（大纲确认/成稿都在那边）
- **冒烟** `_smoke_p5.py` 33 项断言全过（YAML 读写删/非法配置兜底/API 三端点+热更新/前端静态资源一致性）；P3 40 项回归全过

### 3.6 ✅ P5.1：撰写工作台与对话撰写合并为单页流程（2026-08-17 已完成）
用户反馈：两个功能区重复（类型选择器、主题、入口、进度割裂）。已合并为**单页智能撰写**：
- **导航收敛为三项**：✍️ 智能撰写 / 🗂 材料管理 / ⚙️ 类型配置；独立 `view-chat` 移除
- **单页按状态显隐的流程卡**：设置卡（类型+主题一行 grid，「开始撰写」与「💬 对话式撰写」并排两个入口）→ 对话澄清卡（通栏，choosing 时显示主题横幅）→ 大纲确认卡 → 成稿与反馈卡；**进度卡常驻页尾**（任何状态可见，对话期也能看日志）
- **设置卡生命周期**：流程启动（clarifying/writing/…）即收起，完成/出错回到 idle/error 恢复——从交互上防重复发起
- **事件不再切视图**：SSE `start`/`outline_ready`/`done` 仅驱动 `setState()` 显隐卡片，视线不跳页
- **顺手修复存量 bug**：材料管理页上传控件与撰写页 **DOM id 重复**（`fileInput`/`uploadScope`/`btnUpload`/`uploadList`，`getElementById` 恒中首个 → 管理页上传失效）。控件改名 `mat*` 前缀，上传逻辑抽 `uploadFilesTo({files,scope,typeId,listEl,btn,inputEl})` 两处复用
- **验证**：`_smoke_p5.py` 用例4 重写（35 断言全过，含 id 唯一性断言）；P3 40 项回归全过；TestClient 实查三视图装配

### 3.7 ✅ P5.2：材料类型完全动态化（2026-08-17 已完成）
用户要求：类型本身（不止结构）也应可动态配置；内置三类型只是默认，同样可改可删。
- **三层类型来源**（registry 统一聚合，编排器/Agent/web 依旧零改动）：
  1. 内置 `src/types/*.py`（带专属提示词的专家型类型）
  2. **自定义** `config/custom_types/<type_id>.yaml` 全量定义——`name`+`sections` 必填，`build_custom_spec()` 派生系统默认（plan_solve + recall_material + rag/auto + 通用撰写专家 system_prompt + 通用 prompts），`material_base_dir=type_id` 自动获得材料目录隔离与上传/管理/RAG 检索全套能力
  3. **覆盖** `config/types/<type_id>.yaml`（diff 式）对内置/自定义一视同仁，且覆盖字段新增 **`name`（改名）**
- **内置删除**：`config/types/_deleted.yaml` 标记隐藏；`_defaults` 留档 → `restore_builtin()` 可恢复；磁盘材料目录始终保留
- **registry 扩展**：`register_custom/unregister/delete_type/restore_builtin/is_custom`；启动加载顺序=内置注册→删除标记→自定义→覆盖（自定义 id 撞内置时跳过保内置）
- **API**：`POST /api/types`（201，type_id 形态校验小写字母开头/≤40 字符，重复 409）+ `DELETE /api/types/{id}`（返回 kind=custom|builtin）；`/api/types` 与 config 序列化带 `origin` 字段；增删走 WRITE_LOCK
- **前端类型管理器**（类型配置页升级）：type_id（只读）+显示名称编辑、「＋ 新建类型」弹层（标识/名称/角色/初始章节）、「删除类型」（内置弹"可恢复"提示、自定义弹"定义删除"提示）、自定义/已覆盖双角标、「恢复默认」按覆盖态禁用
- **冒烟** 用例5/6/7 新增（自定义生命周期/内置删除恢复/API 增删），**63 项断言全过**；P3 40 项回归全过

### 3.4 🟢 P3 及以后
- [x] ~~本地后端兜底(无 Qdrant 时用关键词/章节匹配,`material_mode="local"`)~~ —— P1.6 已实现 `local_backend.py` + `auto` 模式
- [x] **数据表读取工具**（P3.1，2026-08-17）：`src/tools/read_data_table.py`（`read_data_table`），Drafter 在 ReAct 循环中可调，读取 `data/facts|style` 下的 CSV/XLSX 返回 Markdown 表（截断保护：50 行/12 列/单元格 80 字符/总长 3000 字符）；支持 `read_data_table[文件]` 全表、`[文件][关键词]` 行过滤、`[文件][列=值]` 精确过滤；`.csv`/`.xlsx` 已并入 `loader.SUPPORTED_EXTENSIONS`，Web 上传同步放开；路径只取 basename 防穿越；XLSX 依赖 openpyxl，未装时返回明确提示不中断 ReAct
- [x] **评审打分落地**（P3.2，2026-08-17）：`REVIEWER_SCORE_PROMPT`（四维：内容 40/结构 30/语言 20/格式 10）→ `ReviewAgent.score()` 单次 LLM 调用产出 `ReviewResult`（维度分截断到满分、总分求和、评级映射 优秀≥90/良好≥80/合格≥70/待改进≥60/不合格、needs_revision<70）；结果写入 `SectionDraft.metadata["review_result"]`，`draft.meta["review_summary"]` 汇总均分；web `/api/draft` 返回各章评分 + 前端 `score-badge`/`score-detail` 展示；打分失败静默兜底 0 分不影响主流程
- [x] **对话式撰写**（P3.3，2026-08-17）：Web 聊天形态——`POST /api/chat/start` + `/api/chat/{sid}/message`（澄清循环，`CHAT_CLARIFY_PROMPT` 单次 LLM 调用提炼主题+生成下一追问，`ready=True` 进 `choosing` 态）、`force` 或超 5 轮直接进入撰写；澄清消息存 `session.chat_messages`；前端新增 `card-chat` 面板（气泡消息、Enter 发送、「信息够了，直接生成」按钮）；复用既有 prepare→outline→draft 流水线与 SSE 事件（新增 `chat_question`/`chat_ready` 事件）
- [x] **ReAct `Finish[JSON]` 稳定性**（P3.4，2026-08-17）：① `DRAFTER_REACT_PROMPT` 加 5 条硬规则（单 Action、Action 独占一行、仅正文写完后 Finish、事实依据充分时**不调用工具**最多 2 次、严禁把 Thought/Action 写进 content）；② `_salvage_action()` 解析层挽救——模型只输出裸 JSON 或把 Finish 写在非 Action 行时兜底提取；③ 同工具同参数连续调用检测→提示直接 Finish；④ 真实 LLM 单章验证：一轮 `Finish` 完成、未触发多余工具调用
- [ ] 图表生成工具（数据表 → 柱状/折线图，留待真实使用按需补）

---

## 四、关键文件速查(继续工作时的入口)

| 想做什么 | 看哪里 |
|---|---|
| 改流水线/加步骤 | `src/orchestrator.py` |
| 加新材料类型(不写代码) | Web「⚙️ 类型配置」→「＋ 新建类型」,或 `config/custom_types/<type_id>.yaml` 全量定义 |
| 加内置类型(带专属提示词) | `src/types/`(新建文件)+ `src/registry.py`(注册) |
| 改某类型的章节/提示词(不改代码) | Web「⚙️ 类型配置」页 或 `config/types/<type_id>.yaml`(覆盖机制见 `src/type_config.py`) |
| 参考类型 Spec 写法 | `src/types/work_summary.py` / `report.py` / `kpi_plan.py` |
| 改某范式的提示词 | `src/prompts.py` + `src/types/work_summary.py` 的 `custom_prompts` |
| 调 RAG 检索/导入 | `src/materials/rag_backend.py`、`src/materials/manager.py` |
| 材料按类型分目录 | Spec 的 `material_base_dir` + `orchestrator._ingest_materials()` 的 scope 命名空间 |
| 调本地兜底检索 | `src/materials/local_backend.py` |
| 加数据表/图表工具 | `src/tools/read_data_table.py`（仿 `recall_material.py` 工厂模式） |
| 调评审打分 | `src/agents/reviewer.py` 的 `score()` + `src/prompts.py` 的 `REVIEWER_SCORE_PROMPT` |
| 调对话式撰写 | `web/server.py` 的 `/api/chat/*` + `web/static/app.js` 的 `startChat/sendChatMessage` |
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
- [x] **P2 新材料类型**: 汇报(report,结论先行 5 章)+ KPI 计划(kpi_plan,SMART 5 章)Spec 注册,核心零改动验证可扩展性(2026-08-14)
- [x] **P3.1 数据表读取工具**: `read_data_table`(CSV/XLSX,关键词/列过滤,截断保护,防路径穿越),注册进 Drafter ReAct 工具集,上传白名单放开(2026-08-17)
- [x] **P3.2 评审打分落地**: `ReviewAgent.score()` + `REVIEWER_SCORE_PROMPT` 四维打分→`ReviewResult` 真实填充,写入章节 metadata 与 draft.meta 汇总,web 前端展示评分(2026-08-17)
- [x] **P3.3 对话式撰写**: Web 聊天形态(/api/chat/* 澄清循环→choosing→复用主流水线),前端聊天气泡面板(2026-08-17)
- [x] **P3.4 ReAct 稳定性**: 提示词硬规则 + `_salvage_action` 解析挽救 + 循环调用检测,真实 LLM 一轮 Finish 验证通过(2026-08-17)
- [x] **P3.5 冒烟测试**: `_smoke_p3.py` 25 项断言组通过(数据表/打分/ReAct 解析/端到端 stub write 带评分/对话式 API)(2026-08-17)
- [x] **P3.6 材料按类型分目录 + 管理页面**: Spec 加 `material_base_dir`(data/<type>/facts|style),scope 升级为 `"<type>:facts"` 命名空间实现检索隔离;管理 API(列表/上传带类型/删除防穿越/幂等 reingest)+ 前端管理面板;冒烟扩展至 40 项断言全过(2026-08-17)
- [x] **P3.7 前端美化**: 顶部吸顶导航分「撰写工作台 / 材料管理」两视图,状态徽章挪入 header;材料管理独立整页(自带上传区/筛选/表格/同步);CSS 变量统一配色(2026-08-17)
- [x] **P5 类型结构可配置 + 对话独立页**: `config/types/*.yaml` diff 式覆盖(章节骨架/角色提示,机制字段不开放)+ registry 热更新 + 配置 API(三端点)+ Web 类型配置编辑器;对话式撰写从侧栏卡片升级为整页视图,就绪后自动切回工作台;`_smoke_p5.py` 33 断言全过(2026-08-17)
- [x] **P5.1 撰写/对话合并单页**: 导航收敛三项(智能撰写/材料管理/类型配置),设置→对话澄清→大纲→成稿按状态显隐的流程卡,进度卡常驻页尾,设置卡启动即收起;修复材料页上传控件与撰写页 DOM id 重复导致上传失效(改名 mat* + uploadFilesTo 复用);`_smoke_p5.py` 35 断言全过(2026-08-17)
- [x] **P5.2 类型完全动态化**: 三层来源(内置 types/*.py + 自定义 config/custom_types/*.yaml 全量定义 + config/types/*.yaml diff 覆盖含改名);内置可删除(_deleted.yaml 标记,可恢复)与自定义可删(删定义);`POST/DELETE /api/types`;类型配置页升级管理器(新建弹层/删除/改名/双角标);冒烟 63 断言全过(2026-08-17)
- [ ] P4: 图表生成工具 / 其余按需扩展
