"""ReportWriter 编排器：串联材料导入 → 规划 → 撰写 → 评审 → 导出

将三个 Agent 包装与材料层、导出层组装成端到端流水线。
上层（main.ipynb / main.py）只需调用 Orchestrator.write()。
"""

from datetime import datetime
from typing import List, Optional

from .agents.drafter import DraftingAgent
from .agents.planner import PlannerAgent
from .agents.reviewer import ReviewAgent
from .config import get_settings
from .exporter import Exporter
from .materials.manager import MaterialManager
from .models import DocumentDraft, DocumentTypeSpec, SectionDraft
from .registry import get_registry
from .tools.recall_material import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    build_recall_material_tool,
)


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
        print("▸ ReportWriter 编排器初始化完成\n")

    def write(
        self,
        type_id: str,
        topic: str,
        materials_dir: Optional[str] = None,
    ) -> DocumentDraft:
        """撰写一份材料"""
        spec = self.registry.get(type_id)
        if spec is None:
            available = self.registry.list_types()
            raise ValueError(
                f"未知材料类型 '{type_id}'，可用: {available}"
            )

        self.stats["start_time"] = datetime.now()

        print(f"\n{'='*70}")
        print(f"▸ 开始撰写：{spec.name} —— {topic}")
        print(f"{'='*70}\n")

        # ① 导入参考材料
        materials_dir = materials_dir or self.settings.data_dir
        if spec.material_mode != "none" and self.material.ready:
            print(f"▸ 第一步：导入参考材料（{self.material.effective_mode} 模式）")
            ingest_result = self.material.ingest(materials_dir)
            self.stats["material_hits"] = ingest_result.get("success", 0)
        elif spec.material_mode != "none" and not self.material.ready:
            print(f"▸️  参考材料后端未就绪（{self.material.error}），跳过导入")
            self.stats["material_hits"] = 0

        # ② 规划章节大纲
        print("\n▸ 第二步：规划章节大纲（Plan-and-Solve）")
        outline = self.planner.plan(spec, topic)
        doc_title = outline.get("title") or f"{spec.name}：{topic}"

        # ③ 逐节撰写
        print("\n▸ 第三步：逐节撰写（ReAct + 参考材料）")
        sections: List[SectionDraft] = []
        for idx, sec_spec in enumerate(spec.sections, 1):
            print(f"\n{'─'*70}")
            print(f"▸ 第 {idx}/{len(spec.sections)} 章: {sec_spec.title}")
            print(f"{'─'*70}")
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

        # ④ 评审与修订
        if spec.review_enabled:
            print(f"\n{'='*70}")
            print("▸ 第四步：评审与修订（Reflection）")
            print(f"{'='*70}")
            for sec in sections:
                self.reviewer.review(sec, spec)

        # ⑤ 组装草稿
        self.stats["end_time"] = datetime.now()
        duration = (
            self.stats["end_time"] - self.stats["start_time"]
        ).total_seconds()

        draft = DocumentDraft(
            type_id=type_id,
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
        print(f"\n{'='*70}")
        print("▸ 第五步：导出（Markdown + DOCX）")
        print(f"{'='*70}")
        Exporter.export(draft, spec, self.settings.output_dir)

        print(f"\n{'='*70}")
        print(f"▸ 撰写完成！总字数 {draft.total_words()}，耗时 {duration:.1f} 秒")
        print(f"   类型: {spec.name} | 主题: {topic}")
        print(f"{'='*70}\n")

        return draft

    def _get_section_refs(self, sec_spec, spec: DocumentTypeSpec) -> str:
        """主动注入：按章节检索参考材料片段"""
        if spec.material_mode == "none" or not self.material.ready:
            return "（未启用参考材料）"
        query = f"{sec_spec.title} {sec_spec.hints}"
        return self.material.get_relevant(query, top_k=spec.material_top_k)

    def list_types(self) -> List[str]:
        return self.registry.list_types()
