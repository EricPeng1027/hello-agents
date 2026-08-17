"""P3 冒烟测试：评审打分 + 数据表工具 + 对话式撰写 + ReAct 稳定性（stub LLM）

运行: python _smoke_p3.py
全部断言通过时打印 "✅ P3 冒烟测试全部通过"。
"""

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------- stub LLM
# 三 Agent 共享 LLMService 单例 → patch 单例本身即覆盖所有调用方

PLAN_JSON = json.dumps(
    {
        "title": "2026年Q2研发部工作总结",
        "sections": [
            {
                "key": k,
                "title": t,
                "summary": f"{t}概述",
                "key_points": ["要点A", "要点B"],
            }
            for k, t in [
                ("overview", "工作概述"),
                ("highlights", "本期工作亮点"),
                ("problems", "问题与不足"),
                ("improvements", "改进措施"),
                ("next_plan", "下季度计划"),
            ]
        ],
    },
    ensure_ascii=False,
)

SECTION_CONTENT = (
    "二季度研发部围绕年初既定目标，统筹推进需求交付与质量保障两大主线。"
    "在交付方面，重点需求按期交付率达到91%，Alpha、Beta 两个核心项目均按计划完成里程碑；"
    "在质量方面，系统可用性稳定在99.98%，核心接口RT下降31%，线上缺陷密度持续收敛。"
    "团队同步推进工程效能建设，流水线自动化覆盖率显著提升，发布周期明显缩短。"
    "下季度将继续聚焦交付确定性与系统稳定性，确保年度目标如期达成。"
    * 10
)

SECTION_FINISH_JSON = json.dumps(
    {
        "key": "overview",
        "title": "工作概述",
        "content": SECTION_CONTENT,
        "word_count": 0,  # 由解析层回填
    },
    ensure_ascii=False,
)

SCORE_JSON = json.dumps(
    {
        "dimension_scores": {
            "content_quality": 36,
            "structure_logic": 26,
            "language": 17,
            "format_spec": 9,
        },
        "feedback": {
            "content_quality": "数据翔实",
            "structure_logic": "层次分明",
            "language": "简洁专业",
            "format_spec": "字数达标",
        },
        "summary": "整体质量良好",
    },
    ensure_ascii=False,
)

# 对话澄清的预设响应：第一轮追问、第二轮 ready
CLARIFY_RESPONSES = [
    json.dumps(
        {"ready": False, "topic": "Q2研发总结", "question": "总结的时间范围和部门是？"},
        ensure_ascii=False,
    ),
    json.dumps(
        {"ready": True, "topic": "2026年Q2研发部工作总结", "question": ""},
        ensure_ascii=False,
    ),
]


def make_llm_response(content: str):
    class _Resp:
        def __init__(self, c):
            self.content = c

    return _Resp(content)


class StubLLM:
    """按 prompt 内容路由的桩 LLM（覆盖 planner/drafter/reviewer/chat）"""

    clarify_calls = 0

    def invoke(self, messages):
        prompt = messages[-1]["content"] if messages else ""

        # 对话澄清
        if "多轮对话明确撰写需求" in prompt:
            resp = CLARIFY_RESPONSES[min(self.clarify_calls, len(CLARIFY_RESPONSES) - 1)]
            self.clarify_calls += 1
            return make_llm_response(resp)

        # 评审打分
        if "评分维度" in prompt and "dimension_scores" in prompt:
            return make_llm_response(SCORE_JSON)

        # 反思（自评）→ 无需改进
        if "请从以下维度评审" in prompt:
            return make_llm_response("无需改进")

        # Planner 规划
        if "规划步骤" in prompt and "步骤1" in prompt:
            return make_llm_response(
                '["步骤1: 分析主题与目标读者，明确材料定位", "步骤2: 组装完整的章节大纲"]'
            )
        # Planner 执行
        if "当前步骤" in prompt and "规划步骤" in prompt:
            return make_llm_response(PLAN_JSON)

        # ReAct 撰写 → 一轮 Finish
        if "可用工具" in prompt and "执行历史" in prompt:
            return make_llm_response(
                f"Thought: 事实依据已充分，直接撰写\nAction: Finish[{SECTION_FINISH_JSON}]"
            )

        # 兜底 SimpleAgent.run 也走 invoke
        return make_llm_response(SECTION_CONTENT)

    def think(self, messages, **kw):
        return iter([self.invoke(messages).content])

    def stream_invoke(self, messages, **kw):
        return iter([self.invoke(messages).content])


stub = StubLLM()

# 在导入项目模块前 patch 掉单例，避免真实 LLM 初始化
from src.agents.llm_service import LLMService

LLMService._instance = stub

from src.materials.manager import SCOPE_FACTS, MaterialManager
from src.models import SectionDraft
from src.orchestrator import ReportWriterOrchestrator
from src.tools.read_data_table import build_read_data_table_tool

PASS = []


def check(name: str, cond: bool, detail: str = ""):
    status = "✅" if cond else "❌"
    print(f"{status} {name}" + (f" — {detail}" if detail else ""))
    PASS.append((name, bool(cond)))


# ------------------------------------------------------------------ 用例
def case_read_data_table():
    print("\n[用例1] read_data_table 工具（按类型分目录后）")
    tool = build_read_data_table_tool("data")

    # 1a. 读全表（q2_metrics.csv 已迁入 work_summary/facts）
    out = tool("q2_metrics.csv")
    check("1a 读全表含表头与数据", "项目" in out and "Alpha" in out and "99.98%" in out)

    # 1b. 关键词过滤
    out = tool("q2_metrics.csv][Gamma")
    check("1b 关键词过滤只留 Gamma", "Gamma" in out and "Alpha" not in out)

    # 1c. 列精确过滤
    out = tool("q2_metrics.csv][指标=系统可用性")
    check("1c 列精确过滤", "99.98%" in out and "接口RT降幅" not in out)

    # 1d. 文件不存在 → 友好提示而非异常
    out = tool("nonexistent.csv")
    check("1d 缺文件友好提示", "未找到数据表" in out)

    # 1e. 路径穿越被拒（只取 basename）
    out = tool("../../../etc/passwd.csv")
    check("1e 路径穿越被拦截", "未找到数据表" in out)


def case_typed_material_dirs():
    print("\n[用例6] 材料按类型分目录（隔离 + 检索命中）")
    from src.materials.manager import SCOPE_FACTS, SCOPE_STYLE

    orch = ReportWriterOrchestrator(material_namespace="smoke_p3_typed")
    spec = orch.registry.get("work_summary")
    check("6a Spec 有 material_base_dir", spec.material_base_dir == "work_summary")

    # 端到端：write 应从 data/work_summary/ 导入，且 scope 带类型命名空间
    captured = {}
    orig_get = orch.material.get_relevant

    def spy(query, top_k=3, scope=None):
        captured.setdefault("scopes", set()).add(scope)
        return orig_get(query, top_k=top_k, scope=scope)

    orch.material.get_relevant = spy
    outline = orch.prepare("work_summary", "2026年Q2研发部工作总结")
    draft = orch.draft_with_outline(outline, export=False)

    scopes = captured.get("scopes", set())
    check("6b 分库模式启用", getattr(orch, "_use_split_materials", False) is True)
    check(
        "6c 检索 scope 带类型命名空间",
        "work_summary:facts" in scopes and "work_summary:style" in scopes,
        f"scopes={scopes}",
    )
    check("6d 导入命中 > 0", orch.stats["material_hits"] > 0,
          f"hits={orch.stats['material_hits']}")

    # 类型间隔离：report 库不应能检索到 work_summary 的事实材料
    orch2 = ReportWriterOrchestrator(material_namespace="smoke_p3_typed")
    res = orch2.material.get_relevant(
        "按期交付率 91%", top_k=2, scope="report:facts"
    )
    check("6e report 库检索不到 work_summary 材料", "91%" not in res)


def case_materials_admin_api():
    print("\n[用例7] 材料管理 API（列表/上传/删除/重导入/安全）")
    import httpx
    from fastapi.testclient import TestClient
    import web.server as server

    _orig_init = httpx.Client.__init__
    httpx.Client.__init__ = lambda self, *a, **kw: _orig_init(
        self, *a, **{**kw, "trust_env": False}
    )
    try:
        with TestClient(server.app) as client:
            # 7a. 列表（全类型）
            r = client.get("/api/materials")
            check("7a 列表 200", r.status_code == 200)
            items = r.json().get("items", [])
            check("7b 列表含 work_summary 材料",
                  any(i["type_id"] == "work_summary" and i["filename"] == "q2_data.md" for i in items))

            # 7c. 按类型过滤
            r = client.get("/api/materials?type_id=report")
            check("7c 按类型过滤", r.status_code == 200
                  and all(i["type_id"] == "report" for i in r.json()["items"]))

            # 7d. 上传到 report/facts
            r = client.post(
                "/api/materials/upload",
                files={"files": ("r_data.md", "# 汇报数据\n季度增长 25%", "text/markdown")},
                data={"scope": "facts", "type_id": "report"},
            )
            check("7d 上传成功", r.status_code == 200
                  and r.json()["saved"] == ["report/facts/r_data.md"], r.text[:150])

            # 7e. 上传后出现在列表
            r = client.get("/api/materials?type_id=report")
            check("7e 列表出现新文件",
                  any(i["filename"] == "r_data.md" for i in r.json()["items"]))

            # 7f. 重导入（幂等）
            r = client.post("/api/materials/reingest?type_id=report")
            check("7f 重导入 200", r.status_code == 200 and r.json().get("ok"), r.text[:150])

            # 7g. 删除
            r = client.delete("/api/materials/report/facts/r_data.md")
            check("7g 删除成功", r.status_code == 200 and r.json().get("ok"))

            # 7h. 删除后不存在的文件 404
            r = client.delete("/api/materials/report/facts/r_data.md")
            check("7h 重复删除 404", r.status_code == 404)

            # 7i. 路径穿越被拒（子路径形式会触发我们主动 422；纯 %2F 形式被
            # Starlette 路由规范化拒绝(405)，两者都算安全拦截）
            r = client.delete("/api/materials/report/facts/sub/..%2Fevil.md")
            check("7i 路径穿越被拒", r.status_code in (404, 405, 422))

            # 7j. 未知类型 422
            r = client.get("/api/materials?type_id=nope")
            check("7j 未知类型 422", r.status_code == 422)
    finally:
        httpx.Client.__init__ = _orig_init


def case_scoring():
    print("\n[用例2] 评审打分落地")
    orch = ReportWriterOrchestrator(material_namespace="smoke_p3_score")
    sec = SectionDraft(key="overview", title="工作概述", content=SECTION_CONTENT)
    from src.models import ReviewResult

    result = orch.reviewer.score(sec, orch.registry.get("work_summary"), target_words=400)
    check("2a 返回 ReviewResult", isinstance(result, ReviewResult))
    check("2b 总分=各维度和", result.score == 36 + 26 + 17 + 9, f"score={result.score}")
    check("2c 评级映射正确", result.grade == "良好", f"grade={result.grade}")
    check("2d 维度分齐", set(result.dimension_scores) == {
        "content_quality", "structure_logic", "language", "format_spec"})
    check("2e to_dict 可序列化", json.dumps(result.to_dict(), ensure_ascii=False) is not None)


def case_write_with_scoring():
    print("\n[用例3] 端到端 write（stub）各章带评分 + meta 汇总")
    orch = ReportWriterOrchestrator(material_namespace="smoke_p3_write")
    outline = orch.prepare("work_summary", "2026年Q2研发部工作总结")
    draft = orch.draft_with_outline(outline, export=False)

    check("3a 五章齐全", len(draft.sections) == 5)
    scored = [s for s in draft.sections if (s.metadata or {}).get("review_result")]
    check("3b 每章都有 review_result", len(scored) == len(draft.sections))
    summary = draft.meta.get("review_summary")
    check("3c meta 有 review_summary", isinstance(summary, dict))
    if summary:
        check("3d 均分=88", summary.get("avg_score") == 88.0, f"avg={summary.get('avg_score')}")


def case_chat_api():
    print("\n[用例4] 对话式撰写 API（TestClient，澄清→确认→规划→成稿）")
    stub.clarify_calls = 0

    # TestClient 的 httpx trust_env 会读系统代理 → 强制 trust_env=False
    import httpx
    from fastapi.testclient import TestClient
    import web.server as server

    _orig_init = httpx.Client.__init__
    httpx.Client.__init__ = lambda self, *a, **kw: _orig_init(
        self, *a, **{**kw, "trust_env": False}
    )
    try:
        with TestClient(server.app) as client:
            # 4a. 发起对话
            r = client.post(
                "/api/chat/start",
                json={"type_id": "work_summary", "message": "帮我写个Q2研发总结"},
            )
            check("4a chat/start 200", r.status_code == 200, r.text[:120])
            sid = r.json()["session_id"]

            # 等第一轮澄清完成（追问）
            sess = _wait_session(server, sid, lambda s: s.status == "clarifying" and len(s.chat_messages) >= 2)
            check("4b 第一轮产生追问", sess is not None and sess.chat_messages[-1]["role"] == "assistant")

            # 4c. 继续回复 → stub 第二轮 ready=True → choosing
            r = client.post(
                f"/api/chat/{sid}/message",
                json={"message": "2026年Q2，研发部，重点交付与质量"},
            )
            check("4c chat/message 200", r.status_code == 200, r.text[:120])
            sess = _wait_session(server, sid, lambda s: s.status == "choosing")
            check("4d 第二轮进入 choosing", sess is not None)
            check("4e 主题已提炼", sess is not None and "2026" in sess.topic)

            # 4f. force 直接生成 → 进入撰写流水线
            r = client.post(f"/api/chat/{sid}/message", json={"message": "", "force": True})
            check("4f force 触发撰写", r.status_code == 200 and r.json().get("decision") == "generate")
            sess = _wait_session(server, sid, lambda s: s.status == "outline_review", timeout=60)
            check("4g 对话会话产出大纲", sess is not None and sess.outline is not None)
    finally:
        httpx.Client.__init__ = _orig_init


def _wait_session(server, sid, pred, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        sess = server.store.get(sid)
        if sess and pred(sess):
            return sess
        time.sleep(0.2)
    return server.store.get(sid)


def case_react_salvage():
    print("\n[用例5] ReAct 稳定性（drafter 解析层）")
    from src.agents.drafter import DraftingAgent

    agent = DraftingAgent.__new__(DraftingAgent)  # 跳过 LLM 初始化

    # 5a. 输出只有 JSON 没包 Finish → salvage 生效
    text = '{"key": "overview", "title": "工作概述", "content": "正文", "word_count": 10}'
    check("5a 裸 JSON 挽救", agent._salvage_action(text) == text)

    # 5b. Finish 不在 Action 行 → 也能捞出
    text2 = f"一些解释文字\nFinish[{SECTION_FINISH_JSON}]\n更多文字"
    salvaged = agent._salvage_action(text2)
    check("5b 行内 Finish 挽救", salvaged is not None and "content" in salvaged)

    # 5c. 纯散文不挽救
    check("5c 散文不挽救", agent._salvage_action("今天天气不错") is None)

    # 5d. 提示词包含新规则
    from src.prompts import DRAFTER_REACT_PROMPT

    check("5d ReAct 提示词含防跑偏规则", "最多调用 2 次工具" in DRAFTER_REACT_PROMPT
          and "严禁在 Finish 的 JSON content 字段里写入" in DRAFTER_REACT_PROMPT)


# ------------------------------------------------------------------ main
if __name__ == "__main__":
    case_read_data_table()
    case_scoring()
    case_react_salvage()
    case_typed_material_dirs()
    case_materials_admin_api()
    case_write_with_scoring()
    case_chat_api()

    failed = [n for n, ok in PASS if not ok]
    print(f"\n{'='*50}")
    if failed:
        print(f"❌ {len(failed)} 项失败: {failed}")
        sys.exit(1)
    print(f"✅ P3 冒烟测试全部通过（{len(PASS)} 项断言）")
