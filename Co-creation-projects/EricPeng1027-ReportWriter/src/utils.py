"""工具函数"""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional


def get_current_timestamp(fmt: str = "%Y%m%d_%H%M%S") -> str:
    """当前时间戳字符串，用于输出目录命名"""
    return datetime.now().strftime(fmt)


def safe_filename(name: str) -> str:
    """生成安全的文件名（去除非法字符）"""
    cleaned = "".join(
        c for c in name if c.isalnum() or c in (" ", "-", "_", "，", "。", "、")
    ).strip()
    return cleaned.replace(" ", "_") or "output"


class JSONExtractor:
    """从 LLM 文本响应中提取 JSON

    Agent 常把 JSON 包裹在 ```json ``` 代码块或夹杂解释文字中，
    此工具按优先级尝试多种方式提取并校验。
    """

    @staticmethod
    def extract(
        text: str,
        required_fields: Optional[List[str]] = None,
        fallback_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """提取并校验 JSON

        Args:
            text: LLM 响应文本
            required_fields: 必须存在的字段，缺失则抛错
            fallback_fields: 对缺失字段补充默认值
        """
        if not text or not text.strip():
            raise ValueError("响应为空，无法提取 JSON")

        data: Optional[Dict[str, Any]] = None

        # 1) 优先提取 ```json ... ``` 代码块
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            data = JSONExtractor._safe_loads(fence_match.group(1))

        # 2) 再尝试找第一个 { ... }（贪婪到匹配的右括号）
        if data is None:
            data = JSONExtractor._extract_first_object(text)

        if data is None or not isinstance(data, dict):
            raise ValueError(f"未能从响应中提取到 JSON 对象: {text[:200]}...")

        # 补充默认值
        if fallback_fields:
            for k, v in fallback_fields.items():
                data.setdefault(k, v)

        # 校验必填字段
        if required_fields:
            missing = [f for f in required_fields if f not in data]
            if missing:
                raise ValueError(f"JSON 缺少必填字段: {missing}")

        return data

    @staticmethod
    def _safe_loads(s: str) -> Optional[Dict[str, Any]]:
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _extract_first_object(text: str) -> Optional[Dict[str, Any]]:
        """提取第一个平衡的 {...} JSON 对象"""
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return JSONExtractor._safe_loads(text[start : i + 1])
        return None


def parse_react_output(text: str):
    """解析 ReAct 输出的 Thought 与 Action

    兼容框架原生 ReActAgent 的格式：
        Thought: ...
        Action: tool_name[input] 或 Finish[最终答案]
    """
    thought_match = re.search(r"Thought:\s*(.*?)(?=\nAction:|$)", text, re.DOTALL)
    action_match = re.search(r"Action:\s*(.*?)$", text, re.DOTALL)
    thought = thought_match.group(1).strip() if thought_match else None
    action = action_match.group(1).strip() if action_match else None
    return thought, action


def count_words(text: str) -> int:
    """粗略统计字数（中文按字符计，西文按空格分词后计词）"""
    if not text:
        return 0
    # 去除 Markdown 标记符号，避免计入
    cleaned = re.sub(r"[#*_`>\-\[\]]", "", text)
    # 中文字符数
    cn = len(re.findall(r"[一-鿿]", cleaned))
    # 西文词数
    en = len(re.findall(r"[A-Za-z]+", cleaned))
    return cn + en
