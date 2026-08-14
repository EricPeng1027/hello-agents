# -*- coding: utf-8 -*-
"""P1.9 用户反馈修订 冒烟测试（stub LLM，不走真实 API）

用法: python _smoke_revise.py（项目根目录，测试后可删除）
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from src.models import DocumentDraft, RevisionFeedback, SectionDraft  # noqa: E402
from src.orchestrator import ReportWriterOrchestrator  # noqa: E402


class _StubResp:
    def __init__(self, content):
        self.content = content


def main():
    orch = ReportWriterOrchestrator(material_namespace="reportwriter_smoke")

    draft = DocumentDraft(
        type_id="work_summary",
        title="2026年Q2研发部工作总结",
        sections=[
            SectionDraft(
                key="overview", title="总体概述",
                content="二季度整体进展顺利，完成率达标。", word_count=20,
            ),
            SectionDraft(
                key="highlights", title="重点工作与亮点",
                content="智能客服一期提前上线，年化节约人力成本约18万元。", word_count=24,
            ),
        ],
        meta={},
    )

    calls = []

    def fake_invoke(messages):
        user = messages[-1]["content"]
        calls.append(user)
        return _StubResp(f"[已按意见修订] 原文要点保留，补充正式表述。（{len(user)}字符提示）")

    # 三 Agent 共享 LLMService 单例，patch 单例本身即可（P1.6 踩坑记录）
    with patch.object(orch.reviewer.llm, "invoke", side_effect=fake_invoke):
        out = orch.revise(draft, feedback="整体语气再正式一些，数据以事实材料为准")

    # ① 每章恰好一次 LLM 调用
    assert len(calls) == 2, f"期望 2 次 LLM 调用，实际 {len(calls)}"
    # ② 修订生效、字数更新、metadata 记录
    for sec in out.sections:
        assert sec.content.startswith("[已按意见修订]"), f"{sec.key} 内容未更新"
        assert sec.metadata.get("user_revised") is True
        hist = sec.metadata.get("revision_history")
        assert hist and hist[0]["feedback"].startswith("整体语气"), f"{sec.key} 缺修订历史"
        assert hist[0]["words_before"] in (20, 24)
    # ③ prompt 包含意见 + facts 检索片段（本地后端兜底也应有内容）
    assert "整体语气再正式一些" in calls[0]
    assert "q2_data.md" in calls[0] or "【来源" in calls[0], \
        "facts 检索片段未注入 prompt（分库 facts 检索可能失效）"
    # ④ 修订轮次记录
    rounds = out.meta.get("revision_rounds")
    assert rounds and rounds[0]["sections"] == ["overview", "highlights"]
    assert rounds[0]["words_after"] == out.total_words()
    # ⑤ 多轮修订（reingest=False）
    with patch.object(orch.reviewer.llm, "invoke", side_effect=fake_invoke):
        orch.revise(out, "再补充一项资源不足", reingest=False, export=False)
    assert len(out.meta["revision_rounds"]) == 2
    assert len(out.sections[0].metadata["revision_history"]) == 2

    # ⑥ 按章节反馈（WebUI 形态，P1.10 起合法）：只修订目标章节
    draft_ps = DocumentDraft(
        type_id="work_summary",
        title="按章节测试",
        sections=[
            SectionDraft(key="overview", title="总体概述",
                         content="概述原文", word_count=4),
            SectionDraft(key="highlights", title="重点工作与亮点",
                         content="亮点原文", word_count=4),
        ],
        meta={},
    )
    calls_ps = []

    def fake_invoke_ps(messages):
        calls_ps.append(messages[-1]["content"])
        return _StubResp("[章节修订] 新内容")

    with patch.object(orch.reviewer.llm, "invoke", side_effect=fake_invoke_ps):
        orch.revise(draft_ps, {"highlights": "只改这章"}, reingest=False,
                    export=False)
    assert len(calls_ps) == 1, f"按章节反馈应只调用 1 次 LLM，实际 {len(calls_ps)}"
    assert "只改这章" in calls_ps[0]
    assert draft_ps.sections[1].content == "[章节修订] 新内容"
    assert draft_ps.sections[0].content == "概述原文", "无意见章节不应被修改"
    assert draft_ps.meta["revision_rounds"][0]["per_section"] is True
    assert draft_ps.meta["revision_rounds"][0]["sections"] == ["highlights"]
    # ⑥b 混合意见：全局 + 章节叠加
    fb_mix = RevisionFeedback(global_feedback="全局意见",
                              section_feedback={"overview": "概述专属"})
    assert "全局意见" in fb_mix.feedback_for(draft_ps.sections[0])
    assert "概述专属" in fb_mix.feedback_for(draft_ps.sections[0])
    assert "概述专属" not in fb_mix.feedback_for(draft_ps.sections[1])
    assert not fb_mix.is_empty()
    assert RevisionFeedback().is_empty()
    # ⑥c 全空意见（dict 值全空白）→ ValueError
    try:
        orch.revise(draft_ps, {"highlights": "   "}, export=False)
        raise AssertionError("全空意见应抛 ValueError")
    except ValueError as e:
        assert "修改意见为空" in str(e)
    # ⑦ 错误路径：空意见 / 未知类型 / 非法类型
    for bad, exc in [("", ValueError), ("   ", ValueError), (123, TypeError)]:
        try:
            orch.revise(draft, bad, export=False)
            raise AssertionError(f"feedback={bad!r} 应抛 {exc.__name__}")
        except exc:
            pass
    try:
        orch.revise(DocumentDraft(type_id="nope", title="t", sections=[]),
                    "意见", export=False)
        raise AssertionError("未知 type_id 应抛 ValueError")
    except ValueError as e:
        assert "未知材料类型" in str(e)
    # ⑧ RevisionFeedback 对象直接传入
    fb = RevisionFeedback(global_feedback="对象形式意见")
    with patch.object(orch.reviewer.llm, "invoke", side_effect=fake_invoke):
        orch.revise(out, fb, reingest=False, export=False)
    assert out.meta["revision_rounds"][-1]["feedback"] == "对象形式意见"

    print("✅ P1.9 冒烟测试全部通过（9 项断言组）")


if __name__ == "__main__":
    main()
