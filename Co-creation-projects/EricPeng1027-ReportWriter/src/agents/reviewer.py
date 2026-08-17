"""ReviewAgent：基于 Reflection 范式自评与修订

为兼容任意 OpenAI 兼容端点（不依赖 Function Calling），采用教程第四章的
原生 Reflection 写法：初始 → 反思 → 精修，底层统一用 HelloAgentsLLM.invoke()。
"""

from typing import Dict, Optional

from hello_agents import HelloAgentsLLM

from ..models import DocumentTypeSpec, ReviewResult, SectionDraft
from ..prompts import REVIEWER_REVISE_PROMPT, REVIEWER_SCORE_PROMPT
from ..utils import JSONExtractor, count_words
from .llm_service import LLMService

# 维度满分（与 REVIEWER_SCORE_PROMPT 权重一致）
DIMENSION_CAPS = {
    "content_quality": 40,
    "structure_logic": 30,
    "language": 20,
    "format_spec": 10,
}


class ReviewAgent:
    """材料评审 Agent（Reflection）"""

    def __init__(self, max_iterations: int = 1):
        self.llm: HelloAgentsLLM = LLMService.get_llm()
        self.max_iterations = max_iterations

    def review(
        self, section: SectionDraft, spec: DocumentTypeSpec
    ) -> SectionDraft:
        """评审并优化单个章节"""
        if not section.content or not section.content.strip():
            print(f"▸ 章节为空，跳过评审: {section.title}")
            return section

        prompts = spec.custom_prompts.get("reviewer")
        if not prompts:
            print(f"▸ 未配置评审提示词，跳过评审: {section.title}")
            return section

        print(f"\n▸ ReviewAgent 评审章节: {section.title}")
        try:
            current = section.content
            for i in range(self.max_iterations):
                # 反思
                reflect_prompt = prompts["reflect"].format(content=current)
                feedback = self._invoke(reflect_prompt, spec.system_prompt)
                if "无需改进" in (feedback or ""):
                    print("   ✅ 反思认为无需改进")
                    break
                # 精修
                refine_prompt = prompts["refine"].format(
                    last_attempt=current, feedback=feedback
                )
                refined = self._invoke(refine_prompt, spec.system_prompt)
                if refined and refined.strip():
                    current = refined.strip()

            section.content = current
            section.word_count = count_words(current)
            section.metadata["reviewed"] = True
            print(f"   修订后字数: {section.word_count}")
        except Exception as e:
            print(f"▸️  评审失败，保留原文: {e}")
            section.metadata["reviewed"] = False
            section.metadata["review_error"] = str(e)

        # 评审完成后做结构化打分（单次 LLM 调用，失败不影响主流程）
        score_prompts = (prompts or {}).get("score") or REVIEWER_SCORE_PROMPT
        target_map = {s.key: s.target_words for s in spec.sections}
        review_result = self.score(
            section, spec, score_prompts, target_map.get(section.key, 0)
        )
        section.metadata["review_result"] = review_result.to_dict()
        if review_result.score > 0:
            print(
                f"   📊 评审得分: {review_result.score}/100（{review_result.grade}）"
            )

        return section

    def score(
        self,
        section: SectionDraft,
        spec: DocumentTypeSpec,
        template: str = REVIEWER_SCORE_PROMPT,
        target_words: int = 0,
    ) -> ReviewResult:
        """对章节做结构化评分（单次 LLM 调用，任何失败返回 0 分兜底）"""
        if not section.content or not section.content.strip():
            return ReviewResult(score=0, grade="未评审", reviewer_notes="章节为空")

        try:
            prompt = template.format(
                section_title=section.title,
                target_words=target_words or "原章篇幅",
                content=section.content,
            )
            raw = self._invoke(prompt, spec.system_prompt)
            data = JSONExtractor.extract(raw, required_fields=["dimension_scores"])
            return self._build_result(data)
        except Exception as e:
            print(f"   ▸️  打分失败（不影响主流程）: {e}")
            return ReviewResult(
                score=0, grade="打分失败", reviewer_notes=str(e)[:200]
            )

    @staticmethod
    def _build_result(data: Dict) -> ReviewResult:
        """把 LLM 返回的评分 JSON 规整为 ReviewResult（维度分截断到满分）"""
        raw_dims = data.get("dimension_scores") or {}
        dims: Dict[str, int] = {}
        for dim, cap in DIMENSION_CAPS.items():
            try:
                dims[dim] = max(0, min(int(raw_dims.get(dim, 0)), cap))
            except (TypeError, ValueError):
                dims[dim] = 0
        total = sum(dims.values())
        grade = (
            "优秀" if total >= 90
            else "良好" if total >= 80
            else "合格" if total >= 70
            else "待改进" if total >= 60
            else "不合格"
        )
        feedback = data.get("feedback") or {}
        if not isinstance(feedback, dict):
            feedback = {"summary": str(feedback)}
        return ReviewResult(
            score=total,
            grade=grade,
            dimension_scores=dims,
            detailed_feedback=feedback,
            needs_revision=total < 70,
            reviewer_notes=str(data.get("summary", ""))[:300],
        )

    def revise_with_feedback(
        self,
        section: SectionDraft,
        feedback: str,
        spec: DocumentTypeSpec,
        target_words: int = 0,
        facts_materials: str = "（暂无相关参考材料）",
    ) -> SectionDraft:
        """按用户意见修订单个章节（单次 LLM 调用，失败保留原文）

        Args:
            section: 待修订章节
            feedback: 用户修改意见（已由编排器按章节解析好）
            spec: 材料类型规格
            target_words: 章节目标字数（0 表示按原章篇幅掌握）
            facts_materials: 事实材料检索片段，保证修订涉及的数据/口径有据可依
        """
        if not section.content or not section.content.strip():
            print(f"▸ 章节为空，跳过用户修订: {section.title}")
            return section

        # 旧 spec 的 reviewer prompts 可能没有 "revise" 键，回退到默认模板
        prompts = spec.custom_prompts.get("reviewer") or {}
        template = prompts.get("revise") or REVIEWER_REVISE_PROMPT

        print(f"\n▸ ReviewAgent 按用户意见修订章节: {section.title}")
        try:
            words_label = str(target_words) if target_words > 0 else "原章篇幅"
            prompt = template.format(
                section_title=section.title,
                content=section.content,
                feedback=feedback,
                facts_materials=facts_materials,
                target_words=words_label,
            )
            revised = self._invoke(prompt, spec.system_prompt)
            if revised and revised.strip():
                words_before = section.word_count
                section.content = revised.strip()
                section.word_count = count_words(section.content)
                section.metadata["user_revised"] = True
                section.metadata.setdefault("revision_history", []).append(
                    {
                        "feedback": feedback,
                        "words_before": words_before,
                        "words_after": section.word_count,
                    }
                )
                print(f"   修订后字数: {section.word_count}（修订前 {words_before}）")
            else:
                print("   ▸️  修订结果为空，保留原文")
                section.metadata["user_revision_error"] = "empty_result"
        except Exception as e:
            print(f"▸️  用户修订失败，保留原文: {e}")
            section.metadata["user_revision_error"] = str(e)

        return section

    def _invoke(self, user_prompt: str, system_prompt: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        resp = self.llm.invoke(messages)
        return resp.content if hasattr(resp, "content") else str(resp)
