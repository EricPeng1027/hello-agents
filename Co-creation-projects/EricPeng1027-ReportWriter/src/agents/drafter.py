"""DraftingAgent：基于 ReAct 范式逐节撰写

为兼容任意 OpenAI 兼容端点（不依赖 Function Calling），采用教程第四章的
原生 ReAct 写法：Thought / Action / Observation 文本循环，Finish[...] 收尾。
底层统一用 HelloAgentsLLM.invoke()，工具由本地 ToolExecutor 执行。

参考材料双模式：
- 主动注入：编排器已把相关片段拼进任务（reference_materials），Agent 必定可见
- 被动召回：Agent 可在 ReAct 循环中调用 recall_material 工具按需细查
"""

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from hello_agents import HelloAgentsLLM, SimpleAgent

from ..models import DocumentTypeSpec, SectionSpec
from ..utils import JSONExtractor, count_words
from .llm_service import LLMService


class _ToolExecutor:
    """轻量工具执行器：name -> (description, func)"""

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, description: str, func: Callable[[str], str]):
        self._tools[name] = {"description": description, "func": func}

    def get(self, name: str) -> Optional[Callable]:
        return self._tools.get(name, {}).get("func")

    def describe(self) -> str:
        return "\n".join(f"- {n}: {d['description']}" for n, d in self._tools.items())


class DraftingAgent:
    """材料撰写 Agent（ReAct）"""

    def __init__(self, max_steps: int = 6):
        self.llm: HelloAgentsLLM = LLMService.get_llm()
        self.max_steps = max_steps
        self.tool_executor = _ToolExecutor()

    def register_tool(self, name: str, description: str, func: Callable[[str], str]):
        """注册撰写期可用的工具（如 recall_material）"""
        self.tool_executor.register(name, description, func)

    def draft(
        self,
        section: SectionSpec,
        outline: Dict[str, Any],
        reference_materials: str,
        spec: DocumentTypeSpec,
    ) -> Dict[str, Any]:
        """撰写单个章节，返回 {key,title,content,word_count}"""
        task = self._build_task(section, outline, reference_materials, spec)
        react_prompt = spec.custom_prompts["drafter_react"]

        print(f"\n▸ DraftingAgent 撰写章节: {section.title}")
        try:
            response = self._react_run(task, react_prompt, spec.system_prompt)
            data = self._parse_response(response, section)
            # 内容过短或过低于目标字数，视为撰写不完整，回退重写
            if self._is_incomplete(data, section):
                print(
                    f"   ▸️  章节内容不达标（{data.get('word_count', 0)}/"
                    f"{section.target_words} 字），回退重写"
                )
                data = self._fallback_draft(section, task, spec)
            return data
        except Exception as e:
            print(f"▸️  ReAct 撰写失败，回退到 SimpleAgent: {e}")
            return self._fallback_draft(section, task, spec)

    @staticmethod
    def _is_incomplete(data: Dict[str, Any], section: SectionSpec) -> bool:
        """判断撰写结果是否不完整（需回退重写）"""
        wc = data.get("word_count", 0)
        if wc < 30:
            return True
        # 低于目标字数的 40% 视为明显不完整
        if section.target_words > 0 and wc < section.target_words * 0.4:
            return True
        return False

    # ----------------------------------------------------------- ReAct loop
    def _react_run(
        self, task: str, react_prompt: str, system_prompt: str
    ) -> str:
        """原生 ReAct 循环（参考第四章示例）"""
        history: List[str] = []
        tools_desc = self.tool_executor.describe() or "（无可用工具）"

        for step in range(1, self.max_steps + 1):
            prompt = react_prompt.format(
                tools=tools_desc, question=task, history="\n".join(history) or "无"
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            resp = self.llm.invoke(messages)
            text = resp.content if hasattr(resp, "content") else str(resp)
            if not text:
                break

            thought, action = self._parse_output(text)
            if thought:
                print(f"   🤔 {thought[:80]}")
            if not action:
                break

            if action.startswith("Finish"):
                final = self._parse_action_input(action)
                print(f"   🎉 完成撰写")
                return final

            tool_name, tool_input = self._parse_action(action)
            if not tool_name:
                history.append("Observation: 无效的 Action 格式")
                continue

            func = self.tool_executor.get(tool_name)
            observation = (
                func(tool_input) if func else f"未找到工具 '{tool_name}'"
            )
            print(f"   🎬 {tool_name}[{tool_input[:40]}]")
            history.append(f"Action: {action}")
            history.append(f"Observation: {observation}")

        return ""

    @staticmethod
    def _parse_output(text: str) -> Tuple[Optional[str], Optional[str]]:
        thought = re.search(r"Thought:\s*(.*?)(?=\nAction:|$)", text, re.DOTALL)
        action = re.search(r"Action:\s*(.*?)$", text, re.DOTALL)
        return (
            thought.group(1).strip() if thought else None,
            action.group(1).strip() if action else None,
        )

    @staticmethod
    def _parse_action(action_text: str) -> Tuple[Optional[str], Optional[str]]:
        m = re.match(r"(\w+)\[(.*)\]", action_text, re.DOTALL)
        return (m.group(1), m.group(2)) if m else (None, None)

    @staticmethod
    def _parse_action_input(action_text: str) -> str:
        m = re.match(r"\w+\[(.*)\]", action_text, re.DOTALL)
        return m.group(1).strip() if m else ""

    # ----------------------------------------------------------- task & parse
    def _build_task(
        self,
        section: SectionSpec,
        outline: Dict[str, Any],
        reference_materials: str,
        spec: DocumentTypeSpec,
    ) -> str:
        sec_outline = self._find_section_outline(section.key, outline)
        outline_summary = "（无）"
        if sec_outline:
            points = sec_outline.get("key_points", [])
            summary = sec_outline.get("summary", "")
            outline_summary = f"本章概述: {summary}\n要点:\n" + "\n".join(
                f"- {p}" for p in points
            )

        return spec.custom_prompts["drafter_task"].format(
            section_key=section.key,
            section_title=section.title,
            section_hints=section.hints,
            target_words=section.target_words,
            outline_summary=outline_summary,
            reference_materials=reference_materials,
        )

    @staticmethod
    def _find_section_outline(
        key: str, outline: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        for sec in outline.get("sections", []):
            if sec.get("key") == key:
                return sec
        return None

    def _parse_response(
        self, response: str, section: SectionSpec
    ) -> Dict[str, Any]:
        if not response or not response.strip():
            return {
                "key": section.key,
                "title": section.title,
                "content": "",
                "word_count": 0,
            }
        try:
            data = JSONExtractor.extract(
                response,
                required_fields=["content"],
                fallback_fields={
                    "key": section.key,
                    "title": section.title,
                    "word_count": 0,
                },
            )
        except Exception:
            cleaned = re.sub(
                r"^(Thought|Action|Observation):.*?$",
                "",
                response,
                flags=re.MULTILINE | re.DOTALL,
            ).strip()
            data = {
                "key": section.key,
                "title": section.title,
                "content": cleaned or response.strip(),
                "word_count": 0,
            }
        content = data.get("content", "")
        data["word_count"] = data.get("word_count") or count_words(content)
        print(f"   字数: {data['word_count']}")
        return data

    def _fallback_draft(
        self, section: SectionSpec, task: str, spec: DocumentTypeSpec
    ) -> Dict[str, Any]:
        """回退：用 SimpleAgent（纯对话）直接撰写"""
        from hello_agents import Config as _HelloConfig

        # 关闭框架默认 trace（默认写到 ./memory/traces，会污染当前工作目录）
        _cfg = _HelloConfig()
        _cfg.trace_enabled = False
        # 兜底撰写是纯对话模式，不传 tool_registry，避免对不支持
        # Function Calling 的端点发起 invoke_with_tools 调用
        agent = SimpleAgent(
            name=f"{spec.name}撰写专家（备用）",
            llm=self.llm,
            system_prompt=spec.system_prompt,
            config=_cfg,
        )
        resp = agent.run(task)
        content = resp if isinstance(resp, str) else str(resp)
        content = self._clean_fallback_content(content)
        wc = count_words(content)
        print(f"   （回退撰写）字数: {wc}")
        return {
            "key": section.key,
            "title": section.title,
            "content": content,
            "word_count": wc,
        }

    @staticmethod
    def _clean_fallback_content(text: str) -> str:
        """清洗兜底撰写的输出：剥离可能混入的 ReAct 痕迹"""
        stripped = text.strip()
        # 兜底拿到的是一段 ReAct 对话文本时，优先提取 Finish[...] 里的内容
        m = re.search(r"Finish\[(.*)\]", stripped, re.DOTALL)
        if m:
            inner = m.group(1).strip()
            try:
                data = JSONExtractor.extract(inner, required_fields=["content"])
                return data["content"].strip()
            except Exception:
                return inner
        # 去掉 Thought/Action/Observation 行
        cleaned = re.sub(
            r"^(Thought|Action|Observation):.*?$",
            "",
            stripped,
            flags=re.MULTILINE,
        ).strip()
        return cleaned or stripped
