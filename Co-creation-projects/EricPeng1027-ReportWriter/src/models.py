"""数据模型定义

定义材料类型规格、章节规格、文档草稿、评审结果等核心数据结构。
把"一种材料"抽象成数据规格（DocumentTypeSpec），而非代码，
这是实现多类型可扩展的关键：新增一类材料只需新增一个 Spec。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class SectionSpec:
    """章节规格：描述一种材料里的某一章节

    Attributes:
        key: 章节唯一标识，如 "highlights"
        title: 章节标题，如 "本期工作亮点"
        required: 是否必填
        target_words: 目标字数
        hints: 写作要点提示，指导 Agent 如何撰写该章
    """

    key: str
    title: str
    required: bool = True
    target_words: int = 400
    hints: str = ""


@dataclass
class DocumentTypeSpec:
    """材料类型规格：一种材料的完整定义

    新增材料类型 = 新增一个本类的实例并注册，无需改动核心代码。
    """

    type_id: str  # "work_summary"
    name: str  # "工作总结"
    # 选用的 Agent 范式：plan_solve | react | reflection
    paradigm: str = "plan_solve"
    system_prompt: str = ""
    # 喂给对应框架 Agent 的自定义提示词（结构因范式而异）
    custom_prompts: Dict[str, str] = field(default_factory=dict)
    # 章节骨架
    sections: List[SectionSpec] = field(default_factory=list)
    word_count_total: int = 2000
    # 该类型启用的工具名，如 ["recall_material"]
    tools: List[str] = field(default_factory=lambda: ["recall_material"])
    review_enabled: bool = True
    output_formats: List[str] = field(default_factory=lambda: ["markdown", "docx"])

    # ---- 参考材料配置 ----
    # 参考材料后端：rag | local | none
    material_mode: str = "rag"
    # 每节撰写时主动注入的参考片段数
    material_top_k: int = 3
    # 该类型如何使用参考材料的说明（风格/口径/事实 等）
    material_role_hint: str = ""
    # 事实材料目录（相对 data_dir）：内容必须基于它（数据/事实/口径）
    material_facts_dir: str = "facts"
    # 风格材料目录（相对 data_dir）：仅参考写法与结构，不得照搬内容
    material_style_dir: str = "style"

    def uses_split_material_dirs(self) -> bool:
        """是否启用 事实/风格 分目录模式"""
        return bool(self.material_facts_dir or self.material_style_dir)

    def total_target_words(self) -> int:
        """所有必填章节目标字数之和"""
        return sum(s.target_words for s in self.sections if s.required)


@dataclass
class SectionDraft:
    """某一章节的撰写结果"""

    key: str
    title: str
    content: str = ""
    word_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentDraft:
    """一份完整文档的草稿"""

    type_id: str
    title: str
    sections: List[SectionDraft] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def full_markdown(self) -> str:
        """拼接为完整 Markdown 文本"""
        parts = [f"# {self.title}\n"]
        for sec in self.sections:
            parts.append(f"\n## {sec.title}\n")
            parts.append(sec.content or "")
            parts.append("")
        return "\n".join(parts)

    def total_words(self) -> int:
        return sum(s.word_count for s in self.sections)


@dataclass
class RevisionFeedback:
    """用户修订意见

    Attributes:
        global_feedback: 全文统一意见
        section_feedback: 按章节 key/title 的细粒度意见（WebUI 已启用）
    """

    global_feedback: str = ""
    section_feedback: Dict[str, str] = field(default_factory=dict)

    def feedback_for(self, section: SectionDraft) -> str:
        """取某个章节适用的意见文本（全文意见 + 该章专属意见）

        可能返回空字符串（该章无任何意见），由调用方决定是否跳过。
        """
        parts = []
        if self.global_feedback.strip():
            parts.append(self.global_feedback.strip())
        sec = self.section_feedback.get(section.key) or self.section_feedback.get(
            section.title
        )
        if sec and sec.strip():
            parts.append(sec.strip())
        return "\n".join(parts)

    def is_empty(self) -> bool:
        """全文意见与按章节意见均为空"""
        return not self.global_feedback.strip() and not any(
            v.strip() for v in self.section_feedback.values()
        )


@dataclass
class ReviewResult:
    """评审结果"""

    score: int  # 总分 (0-100)
    grade: str  # 评级
    dimension_scores: Dict[str, int] = field(default_factory=dict)
    detailed_feedback: Dict[str, Any] = field(default_factory=dict)
    needs_revision: bool = False
    revised_content: Optional[str] = None  # 修订后的内容（若发生修订）
    reviewer_notes: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewResult":
        return cls(
            score=data.get("score", 0),
            grade=data.get("grade", "未知"),
            dimension_scores=data.get("dimension_scores", {}),
            detailed_feedback=data.get("detailed_feedback", {}),
            needs_revision=data.get("needs_revision", False),
            revised_content=data.get("revised_content"),
            reviewer_notes=data.get("reviewer_notes", ""),
        )
