"""ReportWriter 编排器：串联材料导入 → 规划 → 撰写 → 评审 → 导出

将三个 Agent 包装与材料层、导出层组装成端到端流水线。
上层（main.ipynb / main.py）调用 write() 一把梭；
Web UI 使用 prepare() + draft_with_outline() 两段式（中间插入大纲确认）。

注意：编排器实例一次只承载一份在写文档（_pending/_use_split_materials/stats
均为单槽实例态）；并发撰写需多实例或外部加锁（web 层用 WRITE_LOCK 串行化）。
"""

from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .agents.drafter import DraftingAgent
from .agents.planner import PlannerAgent
from .agents.reviewer import ReviewAgent
from .config import get_settings
from .exporter import Exporter
from .materials.manager import SCOPE_FACTS, SCOPE_STYLE, MaterialManager
from .models import DocumentDraft, DocumentTypeSpec, RevisionFeedback, SectionDraft
from .prompts import get_drafter_task_template, get_drafter_task_template_single
from .registry import get_registry
from .tools.recall_material import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    build_recall_material_tool,
)

# 进度回调类型：cb({"stage": str, "message": str, ...})
ProgressCallback = Callable[[dict], None]


class ReportWriterOrchestrator:
    """ReportWriter 编排器"""

    def __init__(self, material_namespace: str = "reportwriter"):
        self.settings = get_settings()
        self.registry = get_registry()

        # 材料层（auto：优先 RAG，不可用自动降级为本地后端）
        self.material = MaterialManager(
            mode="auto", namespace=material_namespace
        )

        # 三个 Agent
        self.planner = PlannerAgent()
        self.drafter = DraftingAgent(max_steps=6)
        # 注册 recall_material 工具（被动召回）
        if self.material.ready:
            self.drafter.register_tool(
                TOOL_NAME,
                TOOL_DESCRIPTION,
                build_recall_material_tool(self.material, top_k=3),
            )
        self.reviewer = ReviewAgent(max_iterations=1)

        self.stats = {
            "start_time": None,
            "end_time": None,
            "material_hits": 0,
        }
        # Web UI 进度回调（None 时仅打印，notebook 行为不变）
        self._progress_cb: Optional[ProgressCallback] = None
        # prepare() → draft_with_outline() 之间传递的单槽暂存
        self._pending: Optional[Dict] = None
        print("▸ ReportWriter 编排器初始化完成\n")

    # ------------------------------------------------------------------ emit
    def _emit(self, stage: str, message: str, **extra) -> None:
        """打印进度并回调 web 层（回调异常不阻塞流水线）"""
        print(f"▸ {message}")
        cb = self._progress_cb
        if cb:
            try:
                cb({"stage": stage, "message": message, **extra})
            except Exception:
                pass

    # --------------------------------------------------------------- pipeline
    def write(
        self,
        type_id: str,
        topic: str,
        materials_dir: Optional[str] = None,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> DocumentDraft:
        """撰写一份材料（规划 → 撰写 → 评审 → 导出 一把梭）"""
        outline = self.prepare(type_id, topic, materials_dir, progress_cb)
        return self.draft_with_outline(outline, export=True)

    def prepare(
        self,
        type_id: str,
        topic: str,
        materials_dir: Optional[str] = None,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> Dict:
        """阶段A：导入参考材料 + 规划章节大纲

        Web UI 在此暂停，待用户确认/编辑大纲后调用 draft_with_outline()。
        """
        self._progress_cb = progress_cb
        try:
            spec = self.registry.get(type_id)
            if spec is None:
                available = self.registry.list_types()
                raise ValueError(
                    f"未知材料类型 '{type_id}'，可用: {available}"
                )

            self.stats["start_time"] = datetime.now()

            print(f"\n{'='*70}")
            self._emit("start", f"开始撰写：{spec.name} —— {topic}")
            print(f"{'='*70}\n")

            # ① 导入参考材料
            self._emit("ingest", "第一步：导入参考材料")
            self._ingest_materials(spec, materials_dir)

            # ② 规划章节大纲
            self._emit("plan", "第二步：规划章节大纲（Plan-and-Solve）")
            outline = self.planner.plan(spec, topic)
            if not outline.get("title"):
                outline["title"] = f"{spec.name}：{topic}"

            # 暂存供 draft_with_outline 使用
            self._pending = {"spec": spec, "topic": topic}
            self._emit(
                "outline_ready",
                f"大纲已生成：{outline['title']}（{len(outline.get('sections', []))} 章）",
            )
            return outline
        finally:
            self._progress_cb = None

    def draft_with_outline(
        self,
        outline: Dict,
        export: bool = True,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> DocumentDraft:
        """阶段B：按（可能被用户编辑过的）大纲逐节撰写 → 评审 → 组装 → 导出"""
        if not self._pending:
            raise RuntimeError(
                "请先调用 prepare() 生成大纲，再调用 draft_with_outline()"
            )
        spec: DocumentTypeSpec = self._pending["spec"]
        topic: str = self._pending["topic"]
        doc_title = outline.get("title") or f"{spec.name}：{topic}"

        self._progress_cb = progress_cb
        try:
            # ③ 逐节撰写
            self._emit("draft", "第三步：逐节撰写（ReAct + 参考材料）")
            total = len(spec.sections)
            sections: List[SectionDraft] = []
            for idx, sec_spec in enumerate(spec.sections, 1):
                self._emit(
                    f"draft:{sec_spec.key}",
                    f"第 {idx}/{total} 章: {sec_spec.title}",
                    progress={"current": idx, "total": total},
                )
                refs = self._get_section_refs(sec_spec, spec)
                content_data = self.drafter.draft(sec_spec, outline, refs, spec)
                sections.append(
                    SectionDraft(
                        key=content_data.get("key", sec_spec.key),
                        title=content_data.get("title", sec_spec.title),
                        content=content_data.get("content", ""),
                        word_count=content_data.get("word_count", 0),
                    )
                )
                self._emit(
                    f"draft_done:{sec_spec.key}",
                    f"第 {idx}/{total} 章完成: {sec_spec.title}（{sections[-1].word_count} 字）",
                    progress={"current": idx, "total": total},
                )

            # ④ 评审与修订
            if spec.review_enabled:
                self._emit("review", "第四步：评审与修订（Reflection）")
                for idx, sec in enumerate(sections, 1):
                    self._emit(
                        f"review:{sec.key}",
                        f"评审章节 {idx}/{len(sections)}: {sec.title}",
                    )
                    self.reviewer.review(sec, spec)

            # ⑤ 组装草稿
            self.stats["end_time"] = datetime.now()
            duration = (
                self.stats["end_time"] - self.stats["start_time"]
            ).total_seconds()

            draft = DocumentDraft(
                type_id=spec.type_id,
                title=doc_title,
                sections=sections,
                meta={
                    "duration": duration,
                    "material_hits": self.stats["material_hits"],
                    "paradigm": spec.paradigm,
                    "material_mode": spec.material_mode,
                    "material_backend": self.material.effective_mode,
                    "material_ready": self.material.ready,
                },
            )

            # ⑥ 导出
            if export:
                self._emit("export", "第五步：导出（Markdown + DOCX）")
                paths = Exporter.export(draft, spec, self.settings.output_dir)
                draft.meta["export_paths"] = paths

            self._emit(
                "done",
                f"撰写完成！总字数 {draft.total_words()}，耗时 {duration:.1f} 秒",
            )
            print(f"   类型: {spec.name} | 主题: {topic}\n")
            return draft
        finally:
            self._progress_cb = None
            self._pending = None

    def _ingest_materials(
        self, spec: DocumentTypeSpec, materials_dir: Optional[str] = None
    ) -> None:
        """导入参考材料（write 与 revise 共用）

        分库模式（data/facts/ 或 data/style/ 任一存在）按 scope 分别导入，
        否则走单库模式。结果写入 self.stats["material_hits"] 与
        self._use_split_materials。
        """
        materials_dir = materials_dir or self.settings.data_dir
        split_dirs = spec.uses_split_material_dirs()
        facts_dir = Path(materials_dir) / spec.material_facts_dir
        style_dir = Path(materials_dir) / spec.material_style_dir
        use_split = split_dirs and (facts_dir.is_dir() or style_dir.is_dir())

        if spec.material_mode != "none" and self.material.ready:
            if use_split:
                print(
                    f"▸ 导入参考材料（{self.material.effective_mode} 模式，"
                    "facts/style 分库）"
                )
                total = 0
                if facts_dir.is_dir():
                    r = self.material.ingest(str(facts_dir), scope=SCOPE_FACTS)
                    total += r.get("success", 0)
                if style_dir.is_dir():
                    r = self.material.ingest(str(style_dir), scope=SCOPE_STYLE)
                    total += r.get("success", 0)
                self.stats["material_hits"] = total
            else:
                print(f"▸ 导入参考材料（{self.material.effective_mode} 模式）")
                ingest_result = self.material.ingest(materials_dir)
                self.stats["material_hits"] = ingest_result.get("success", 0)
        elif spec.material_mode != "none" and not self.material.ready:
            print(f"▸️  参考材料后端未就绪（{self.material.error}），跳过导入")
            self.stats["material_hits"] = 0

        self._use_split_materials = use_split

    def revise(
        self,
        draft: DocumentDraft,
        feedback,
        reingest: bool = True,
        export: bool = True,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> DocumentDraft:
        """按用户意见增量修订已生成的草稿（不重新规划/撰写）

        Args:
            draft: write() 返回的文档草稿
            feedback: 用户修改意见
                - str: 全文统一意见
                - Dict[str, str]: 按章节 key/title 的细粒度意见（WebUI）
                - RevisionFeedback: 结构化意见（两者皆可）
            reingest: 是否重新导入参考材料。材料库是内存态，fresh session
                （重启内核后直接修订）必须为 True；同会话多轮修订传 False 省时
            export: 是否导出修订稿（新的时间戳目录，不覆盖原稿）
            progress_cb: Web UI 进度回调

        Returns:
            修订后的 DocumentDraft（原地修改并返回）
        """
        self._progress_cb = progress_cb
        try:
            # ① 解析意见
            fb = self._normalize_feedback(feedback)
            if fb.is_empty():
                raise ValueError("修改意见为空，请提供具体的修改意见")

            spec = self.registry.get(draft.type_id)
            if spec is None:
                available = self.registry.list_types()
                raise ValueError(
                    f"未知材料类型 '{draft.type_id}'，可用: {available}"
                )

            print(f"\n{'='*70}")
            self._emit("revise", f"开始按用户意见修订：{draft.title}")
            print(f"{'='*70}")

            # ② 导入参考材料（fresh session 时事实库为空，必须 reingest）
            if reingest:
                self._emit("ingest", "导入参考材料")
                self._ingest_materials(spec)

            # ③ 逐章修订（ReviewAgent + 事实材料检索）
            per_section = bool(fb.section_feedback)
            self._emit(
                "revise",
                f"逐章修订（{len(draft.sections)} 章，ReviewAgent + facts 检索）"
                + ("，按章节细粒度意见" if per_section else "，全文统一意见"),
            )
            target_map = {s.key: s.target_words for s in spec.sections}
            revised_keys: List[str] = []
            for sec in draft.sections:
                sec_feedback = fb.feedback_for(sec)
                if not sec_feedback.strip():
                    self._emit("revise", f"无修改意见，跳过章节: {sec.title}")
                    continue
                facts_refs = self._get_revision_facts(sec, sec_feedback, spec)
                self.reviewer.revise_with_feedback(
                    sec,
                    sec_feedback,
                    spec,
                    target_words=target_map.get(sec.key, 0),
                    facts_materials=facts_refs,
                )
                revised_keys.append(sec.key)
                self._emit(
                    f"revise_done:{sec.key}",
                    f"章节修订完成: {sec.title}（{sec.word_count} 字）",
                )

            # ④ 记录修订轮次并导出
            draft.meta.setdefault("revision_rounds", []).append(
                {
                    "feedback": fb.global_feedback,
                    "section_feedback": fb.section_feedback or None,
                    "per_section": per_section,
                    "sections": revised_keys,
                    "words_after": draft.total_words(),
                }
            )

            if export:
                self._emit("export", "导出修订稿（Markdown + DOCX）")
                paths = Exporter.export(draft, spec, self.settings.output_dir)
                draft.meta["export_paths"] = paths

            self._emit(
                "done",
                f"修订完成！第 {len(draft.meta['revision_rounds'])} 轮，"
                f"总字数 {draft.total_words()}",
            )
            print()
            return draft
        finally:
            self._progress_cb = None

    @staticmethod
    def _normalize_feedback(feedback) -> RevisionFeedback:
        """把用户意见规整为 RevisionFeedback"""
        if isinstance(feedback, RevisionFeedback):
            return feedback
        if isinstance(feedback, str):
            if not feedback.strip():
                raise ValueError("修改意见为空，请提供具体的修改意见")
            return RevisionFeedback(global_feedback=feedback)
        if isinstance(feedback, dict):
            # 按章节细粒度意见（{章节key或标题: 意见}，WebUI）
            return RevisionFeedback(section_feedback=dict(feedback))
        raise TypeError(
            f"不支持的 feedback 类型: {type(feedback)}（支持 str / Dict[str, str] / RevisionFeedback）"
        )

    def _get_revision_facts(
        self, section: SectionDraft, feedback: str, spec: DocumentTypeSpec
    ) -> str:
        """为修订检索事实材料片段（意见涉及数据/口径时保证有据可依）"""
        empty = "（暂无相关参考材料）"
        if spec.material_mode == "none" or not self.material.ready:
            return empty
        query = f"{section.title} {feedback}"
        if getattr(self, "_use_split_materials", False):
            return self.material.get_relevant(
                query, top_k=spec.material_top_k, scope=SCOPE_FACTS
            )
        return self.material.get_relevant(query, top_k=spec.material_top_k)

    def _get_section_refs(self, sec_spec, spec: DocumentTypeSpec):
        """主动注入：按章节检索参考材料片段

        返回：
        - 分库模式：{"facts": str, "style": str, "split": True}
        - 单库模式：str（向后兼容）
        """
        if spec.material_mode == "none" or not self.material.ready:
            if getattr(self, "_use_split_materials", False):
                return {
                    "facts": "（暂无相关参考材料）",
                    "style": "（暂无相关参考材料）",
                    "split": True,
                }
            return "（未启用参考材料）"

        query = f"{sec_spec.title} {sec_spec.hints}"
        if getattr(self, "_use_split_materials", False):
            return {
                "facts": self.material.get_relevant(
                    query, top_k=spec.material_top_k, scope=SCOPE_FACTS
                ),
                "style": self.material.get_relevant(
                    query, top_k=spec.material_top_k, scope=SCOPE_STYLE
                ),
                "split": True,
            }
        return self.material.get_relevant(query, top_k=spec.material_top_k)

    def list_types(self) -> List[str]:
        return self.registry.list_types()
