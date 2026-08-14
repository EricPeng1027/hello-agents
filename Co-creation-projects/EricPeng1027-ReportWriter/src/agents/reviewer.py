"""ReviewAgent：基于 Reflection 范式自评与修订

为兼容任意 OpenAI 兼容端点（不依赖 Function Calling），采用教程第四章的
原生 Reflection 写法：初始 → 反思 → 精修，底层统一用 HelloAgentsLLM.invoke()。
"""

from typing import Dict

from hello_agents import HelloAgentsLLM

from ..models import DocumentTypeSpec, SectionDraft
from ..prompts import REVIEWER_REVISE_PROMPT
from ..utils import count_words
from .llm_service import LLMService


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

        return section

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
