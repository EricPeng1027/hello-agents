# -*- coding: utf-8 -*-
"""P1.10 Web UI 冒烟测试（stub LLM，不走真实 API）

用法: python _smoke_web.py（项目根目录，测试后可删除）

stub 策略：patch 在 Agent 方法级（PlannerAgent.plan / DraftingAgent.draft /
ReviewAgent.review / revise_with_feedback），而非 LLM 层——更稳，且本测试
目标正是验证 web 层与新拆分/启用的编排路径（prepare/draft_with_outline/
按章节 revise），不是验证 prompt。

注意：不触发 lifespan（启动真实 LLM/RAG 可能联网初始化而卡住），
直接对 module 级 orch/store 依赖测试；静态挂载目录在 import 时已就绪。
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import web.server as server  # noqa: E402
from src.agents.drafter import DraftingAgent  # noqa: E402
from src.agents.planner import PlannerAgent  # noqa: E402
from src.agents.reviewer import ReviewAgent  # noqa: E402
from src.orchestrator import ReportWriterOrchestrator  # noqa: E402


FAKE_OUTLINE = {
    "title": "冒烟测试总结",
    "sections": [
        {"key": "overview", "title": "总体概述", "summary": "概述", "key_points": ["要点1"]},
        {"key": "highlights", "title": "重点工作与亮点", "summary": "亮点", "key_points": ["要点A"]},
    ],
}

revise_calls = []


# httpx.Client.__init__ 打补丁：公司系统代理（trust_env 读 HTTP(S)_PROXY）
# 会把 TestClient 发往 http://testserver 的请求拦到 proxyza，强制 trust_env=False
_orig_httpx_init = httpx.Client.__init__


def _patched_httpx_init(self, *args, **kwargs):
    kwargs["trust_env"] = False
    _orig_httpx_init(self, *args, **kwargs)


def fake_plan(self, spec, topic):
    return FAKE_OUTLINE


def fake_draft(self, section, outline, refs, spec):
    return {
        "key": section.key,
        "title": section.title,
        "content": f"{section.title}的正文内容。",
        "word_count": 10,
    }


def fake_review(self, section, spec):
    section.metadata["reviewed"] = True
    return section


def fake_revise(self, section, feedback, spec, target_words=0, facts_materials=""):
    revise_calls.append(section.key)
    section.content = f"[已修订]{section.content}"
    section.word_count += 4
    section.metadata["user_revised"] = True
    return section


def wait_status(sid, want, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = server.store.get(sid)
        if session and session.status == want:
            return True
        if session and session.status == "error":
            raise AssertionError(f"会话进入 error: {session.error}")
        time.sleep(0.1)
    raise AssertionError(f"等待状态 {want} 超时（当前: {server.store.get(sid).status}）")


def main():
    # 用独立命名空间的真实编排器（材料后端 auto 降级 local，不依赖 Qdrant）。
    # TestClient(不带 "with") 不触发 lifespan，避免启动时联网初始化 LLM/RAG。
    server.orch = ReportWriterOrchestrator(material_namespace="reportwriter_web_smoke")

    with (
        patch.object(PlannerAgent, "plan", fake_plan),
        patch.object(DraftingAgent, "draft", fake_draft),
        patch.object(ReviewAgent, "review", fake_review),
        patch.object(ReviewAgent, "revise_with_feedback", fake_revise),
        # 公司系统代理会拦截本机回环 → httpx 直连（trust_env=False）
        patch.object(httpx.Client, "__init__", _patched_httpx_init),
    ):
        client = TestClient(server.app, raise_server_exceptions=False)

        # ⑪ 静态前端（先验，隔离挂载问题）
        r = client.get("/")
        assert r.status_code == 200 and "ReportWriter" in r.text
        r = client.get("/app.js")
        assert r.status_code == 200
        print("⓪ 静态页面 ✅", flush=True)

        # ① 类型清单
        types = client.get("/api/types").json()
        assert any(t["type_id"] == "work_summary" for t in types)
        ws = next(t for t in types if t["type_id"] == "work_summary")
        assert len(ws["sections"]) == 5 and ws["sections"][0]["key"] == "overview"
        print("① 类型清单 ✅", flush=True)

        # ② 材料上传：md 成功 / exe 拒绝
        resp = client.post(
            "/api/materials/upload",
            files=[("files", ("web_smoke_note.md", "# smoke\n内容".encode("utf-8"), "text/markdown"))],
            data={"scope": "facts"},
        )
        ok = resp.json()
        assert ok["saved"] == ["facts/web_smoke_note.md"], ok
        bad = client.post(
            "/api/materials/upload",
            files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))],
            data={"scope": "facts"},
        ).json()
        assert bad["rejected"] and not bad["saved"]
        bad_scope = client.post(
            "/api/materials/upload",
            files=[("files", ("a.md", b"x", "text/markdown"))],
            data={"scope": "nope"},
        )
        assert bad_scope.status_code == 422
        print("② 材料上传（白名单/scope 校验）✅", flush=True)

        # ③ 发起撰写 → 等 outline_review
        sid = client.post(
            "/api/write", json={"type_id": "work_summary", "topic": "冒烟主题"}
        ).json()["session_id"]
        wait_status(sid, "outline_review")
        print("③ 阶段A 完成（大纲就绪）✅", flush=True)

        # ④ 取大纲
        outline = client.get(f"/api/outline/{sid}").json()
        assert outline["title"] == "冒烟测试总结"
        print("④ 获取大纲 ✅", flush=True)

        # ⑤ 编辑后确认 → 等 done → 取成稿
        outline["title"] = "编辑后的标题"
        outline["sections"][0]["summary"] = "用户改过的概述"
        r = client.post(f"/api/outline/{sid}/confirm", json=outline)
        assert r.status_code == 200, r.text
        wait_status(sid, "done")
        draft = client.get(f"/api/draft/{sid}").json()
        assert draft["title"] == "编辑后的标题"
        assert len(draft["sections"]) == 5  # spec 5 章，outline 只有 2 章 key
        assert draft["export"]["markdown"] is True
        md_path = server.Path(draft_path_of(sid, "markdown"))
        assert md_path.is_file()
        print("⑤ 阶段B 完成（成稿+导出）✅", flush=True)

        # ⑤b 大纲 key 校验：未知 key → 422
        sid2 = client.post(
            "/api/write", json={"type_id": "work_summary", "topic": "校验主题"}
        ).json()["session_id"]
        wait_status(sid2, "outline_review")
        bad_outline = dict(FAKE_OUTLINE)
        bad_outline["sections"] = [{"key": "hacked", "title": "x"}]
        r = client.post(f"/api/outline/{sid2}/confirm", json=bad_outline)
        assert r.status_code == 422, r.text
        print("⑤b 大纲 key 校验 ✅", flush=True)

        # ⑥ 按章节反馈：只修订 highlights 一章
        revise_calls.clear()
        r = client.post(
            f"/api/revise/{sid}", json={"section_feedback": {"highlights": "补充数据"}}
        )
        assert r.status_code == 200, r.text
        wait_status(sid, "done")
        assert revise_calls == ["highlights"], f"应只修订 highlights，实际 {revise_calls}"
        draft2 = client.get(f"/api/draft/{sid}").json()
        assert draft2["revision_rounds"] == 1
        hl = next(s for s in draft2["sections"] if s["key"] == "highlights")
        ov = next(s for s in draft2["sections"] if s["key"] == "overview")
        assert hl["content"].startswith("[已修订]") and hl["user_revised"]
        assert not ov["content"].startswith("[已修订]")
        print("⑥ 按章节反馈（只改目标章）✅", flush=True)

        # ⑦ 全空反馈 → 422
        r = client.post(f"/api/revise/{sid}", json={})
        assert r.status_code == 422
        r = client.post(
            f"/api/revise/{sid}",
            json={"global_feedback": "  ", "section_feedback": {"x": " "}},
        )
        assert r.status_code == 422
        print("⑦ 空反馈 422 ✅", flush=True)

        # ⑧ SSE：直接断言 backlog 可序列化 + 事件结构（TestClient 的 SSE
        # 流式响应在端口复用/连接池下不稳定，浏览器 EventSource 才是真实路径，
        # 真实验证在 README 的手动步骤里覆盖）
        session = server.store.get(sid)
        assert len(session.events) >= 3, "backlog 事件不足"
        import json as _json

        first = _json.loads(_json.dumps(session.events[0]))
        assert "stage" in first and "seq" in first and "message" in first
        stages = {e["stage"] for e in session.events}
        assert "outline_ready" in stages and "done" in stages, stages
        seqs = [e["seq"] for e in session.events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "seq 应单调递增"
        print("⑧ SSE backlog（事件结构/seq 单调）✅", flush=True)

        # ⑨ 下载
        r = client.get(f"/api/download/{sid}/markdown")
        assert r.status_code == 200 and len(r.content) > 0
        r = client.get(f"/api/download/{sid}/docx")
        assert r.status_code == 200 and len(r.content) > 100
        r = client.get(f"/api/download/{sid}/pdf")
        assert r.status_code == 422
        print("⑨ 成稿下载 ✅")

        # ⑩ 并发写锁：撰写中再发起 write → 会话收到 error 事件
        # （锁在阶段线程内获取/释放；用挂起的 revise 制造占用）
        acquired = server.WRITE_LOCK.acquire(blocking=False)
        assert acquired
        try:
            sid3 = client.post(
                "/api/write", json={"type_id": "work_summary", "topic": "并发主题"}
            ).json()["session_id"]
            wait_status(sid3, "error")
            session3 = server.store.get(sid3)
            assert session3.error == "busy"
        finally:
            server.WRITE_LOCK.release()
        print("⑩ 并发写锁（忙时新会话报 error）✅")

        # ⑪ 静态前端
        r = client.get("/")
        assert r.status_code == 200 and "ReportWriter" in r.text
        r = client.get("/app.js")
        assert r.status_code == 200
        print("⑪ 静态页面 ✅")

    print("\n✅ P1.10 Web UI 冒烟测试全部通过（11 项断言组）")


def draft_path_of(sid, kind):
    session = server.store.get(sid)
    return session.draft.meta["export_paths"][kind]


if __name__ == "__main__":
    main()
