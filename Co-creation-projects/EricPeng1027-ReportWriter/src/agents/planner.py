"""PlannerAgent：基于 Plan-and-Solve 范式生成章节大纲

为兼容任意 OpenAI 兼容端点（不依赖 Function Calling），采用教程第四章的
原生 Plan-and-Solve 写法：先让 LLM 生成步骤列表，再逐步执行，最后一步
组装出对齐章节骨架的 JSON 大纲。底层统一用 HelloAgentsLLM.invoke()。
"""

import ast
from typing import Any, Dict, List

from hello_agents import HelloAgentsLLM

from ..models import DocumentTypeSpec
from ..utils import JSONExtractor
from .llm_service import LLMService


class PlannerAgent:
    """材料规划 Agent（Plan-and-Solve）"""

    def __init__(self):
        self.llm: HelloAgentsLLM = LLMService.get_llm()

    def plan(self, spec: DocumentTypeSpec, topic: str) -> Dict[str, Any]:
        """生成章节大纲

        Returns:
            {"title": str, "sections": [{"key","title","summary","key_points","target_words"}]}
        """
        question = self._build_question(spec, topic)
        planner_prompt = spec.custom_prompts["planner"]
        executor_prompt = spec.custom_prompts["executor"]

        print(f"\n▸ PlannerAgent 开始规划: {topic}")

        # 1. 生成计划（步骤列表）
        plan = self._generate_plan(planner_prompt, question)
        if not plan:
            print("▸️  规划失败，回退到 Spec 章节骨架")
            return self._fallback_outline(spec, topic)

        # 2. 逐步执行，最后一步产出 JSON 大纲
        final_answer = self._execute_plan(
            executor_prompt, question, plan, spec.system_prompt
        )

        # 3. 解析 JSON
        try:
            outline = JSONExtractor.extract(
                final_answer, required_fields=["title", "sections"]
            )
            outline = self._normalize(outline, spec)
            print(f"▸ 规划完成: {outline['title']}，{len(outline['sections'])} 章")
        except Exception as e:
            print(f"▸️  规划 JSON 解析失败，回退到 Spec 章节骨架: {e}")
            outline = self._fallback_outline(spec, topic)

        return outline

    # ----------------------------------------------------------- internals
    def _build_question(self, spec: DocumentTypeSpec, topic: str) -> str:
        section_lines = []
        for s in spec.sections:
            section_lines.append(
                f"- key={s.key} | 标题={s.title} | 目标字数={s.target_words} | 要求={s.hints}"
            )
        sections_block = "\n".join(section_lines)
        return (
            f"材料类型: {spec.name}\n"
            f"撰写主题: {topic}\n"
            f"角色要求: {spec.system_prompt}\n\n"
            f"章节骨架（必须覆盖，key 与标题保持一致）:\n{sections_block}\n\n"
            f"请规划章节大纲。"
        )

    def _generate_plan(self, planner_prompt: str, question: str) -> List[str]:
        prompt = planner_prompt.format(question=question)
        messages = [{"role": "user", "content": prompt}]
        resp = self.llm.invoke(messages)
        text = resp.content if hasattr(resp, "content") else str(resp)
        print("--- 计划已生成 ---")
        # 提取 ```python ... ``` 中的列表
        try:
            plan_str = text.split("```python")[1].split("```")[0].strip()
            plan = ast.literal_eval(plan_str)
            return plan if isinstance(plan, list) else []
        except Exception:
            # 兜底：尝试找任何 [...]
            try:
                start = text.find("[")
                end = text.rfind("]")
                if start != -1 and end != -1:
                    plan = ast.literal_eval(text[start : end + 1])
                    return plan if isinstance(plan, list) else []
            except Exception:
                pass
        return []

    def _execute_plan(
        self,
        executor_prompt: str,
        question: str,
        plan: List[str],
        system_prompt: str,
    ) -> str:
        history = ""
        final_answer = ""
        for i, step in enumerate(plan, 1):
            print(f"-> 执行步骤 {i}/{len(plan)}: {step}")
            prompt = executor_prompt.format(
                question=question,
                plan=plan,
                history=history if history else "无",
                current_step=step,
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            resp = self.llm.invoke(messages)
            text = resp.content if hasattr(resp, "content") else str(resp)
            history += f"步骤 {i}: {step}\n结果: {text}\n\n"
            final_answer = text
        return final_answer

    def _normalize(
        self, outline: Dict[str, Any], spec: DocumentTypeSpec
    ) -> Dict[str, Any]:
        spec_by_key = {s.key: s for s in spec.sections}
        normalized: List[Dict[str, Any]] = []
        for sec in outline.get("sections", []):
            key = sec.get("key", "")
            spec_sec = spec_by_key.get(key)
            normalized.append(
                {
                    "key": key,
                    "title": sec.get("title") or (spec_sec.title if spec_sec else key),
                    "summary": sec.get("summary", ""),
                    "key_points": sec.get("key_points", []),
                    "target_words": spec_sec.target_words if spec_sec else 400,
                }
            )
        existing = {s["key"] for s in normalized}
        for s in spec.sections:
            if s.required and s.key not in existing:
                normalized.append(
                    {
                        "key": s.key,
                        "title": s.title,
                        "summary": s.hints,
                        "key_points": [],
                        "target_words": s.target_words,
                    }
                )
        outline["sections"] = normalized
        return outline

    def _fallback_outline(
        self, spec: DocumentTypeSpec, topic: str
    ) -> Dict[str, Any]:
        return {
            "title": f"{spec.name}：{topic}",
            "sections": [
                {
                    "key": s.key,
                    "title": s.title,
                    "summary": s.hints,
                    "key_points": [],
                    "target_words": s.target_words,
                }
                for s in spec.sections
            ],
        }
