"""ReviewAgent：基于 Reflection 范式自评与修订

为兼容任意 OpenAI 兼容端点（不依赖 Function Calling），采用教程第四章的
原生 Reflection 写法：初始 → 反思 → 精修，底层统一用 HelloAgentsLLM.invoke()。
"""

from typing import Dict

from hello_agents import HelloAgentsLLM

from ..models import DocumentTypeSpec, SectionDraft
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

    def _invoke(self, user_prompt: str, system_prompt: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        resp = self.llm.invoke(messages)
        return resp.content if hasattr(resp, "content") else str(resp)
