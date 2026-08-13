"""导出器：将文档草稿导出为 Markdown 与 DOCX"""

import json
import os
from pathlib import Path
from typing import List

from .models import DocumentDraft, DocumentTypeSpec
from .utils import get_current_timestamp, safe_filename


class Exporter:
    """文档导出器"""

    @staticmethod
    def export(
        draft: DocumentDraft,
        spec: DocumentTypeSpec,
        output_dir: str = "outputs",
        formats: List[str] = None,
    ) -> dict:
        """导出文档到多种格式

        Args:
            draft: 文档草稿
            spec: 材料类型规格（用于文件命名与格式选择）
            output_dir: 输出根目录
            formats: 导出格式列表，默认取 spec.output_formats

        Returns:
            {"dir": ..., "files": {"markdown": ..., "docx": ..., "meta": ...}}
        """
        formats = formats or spec.output_formats
        ts = get_current_timestamp()
        sub_dir = Path(output_dir) / ts
        sub_dir.mkdir(parents=True, exist_ok=True)

        base_name = f"{spec.name}_{safe_filename(draft.title)}"
        paths: dict = {"dir": str(sub_dir)}

        # Markdown
        if "markdown" in formats:
            md_path = sub_dir / f"{base_name}.md"
            Exporter._export_markdown(draft, spec, md_path)
            paths["markdown"] = str(md_path)
            print(f"▸ 已导出 Markdown: {md_path}")

        # DOCX
        if "docx" in formats:
            try:
                docx_path = sub_dir / f"{base_name}.docx"
                Exporter._export_docx(draft, spec, docx_path)
                paths["docx"] = str(docx_path)
                print(f"▸ 已导出 DOCX: {docx_path}")
            except Exception as e:
                print(f"▸️  DOCX 导出失败（缺少 python-docx?）: {e}")
                paths["docx"] = None

        # 元数据 JSON
        meta_path = sub_dir / "meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "type_id": draft.type_id,
                    "type_name": spec.name,
                    "title": draft.title,
                    "total_words": draft.total_words(),
                    "sections": [
                        {
                            "key": s.key,
                            "title": s.title,
                            "word_count": s.word_count,
                            "metadata": s.metadata,
                        }
                        for s in draft.sections
                    ],
                    "meta": draft.meta,
                    "exported_at": ts,
                },
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        paths["meta"] = str(meta_path)
        print(f"▸ 已导出元数据: {meta_path}")

        return paths

    @staticmethod
    def _export_markdown(
        draft: DocumentDraft, spec: DocumentTypeSpec, path: Path
    ) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {draft.title}\n\n")
            for sec in draft.sections:
                f.write(f"## {sec.title}\n\n")
                f.write(sec.content or "")
                f.write("\n\n")

            # 元数据附录
            f.write("---\n\n")
            f.write(f"## 文档信息\n\n")
            f.write(f"- **材料类型**: {spec.name}\n")
            f.write(f"- **总字数**: {draft.total_words()}\n")
            f.write(f"- **范式**: {spec.paradigm}\n")
            f.write(f"- **参考材料模式**: {spec.material_mode}\n")
            if draft.meta.get("material_hits") is not None:
                f.write(f"- **参考材料命中片段**: {draft.meta['material_hits']}\n")
            if draft.meta.get("duration"):
                f.write(f"- **耗时**: {draft.meta['duration']:.1f} 秒\n")
            f.write("\n## 章节字数\n\n")
            for s in draft.sections:
                f.write(f"- {s.title}: {s.word_count} 字\n")

    @staticmethod
    def _export_docx(
        draft: DocumentDraft, spec: DocumentTypeSpec, path: Path
    ) -> None:
        from docx import Document
        from docx.shared import Pt

        doc = Document()

        # 标题
        h = doc.add_heading(draft.title, level=0)

        # 各章节
        for sec in draft.sections:
            doc.add_heading(sec.title, level=1)
            # 按段落拆分正文
            for para in (sec.content or "").split("\n"):
                text = para.strip()
                if not text:
                    continue
                # 简易识别二级/三级 Markdown 标题
                if text.startswith("### "):
                    doc.add_heading(text[4:].strip(), level=3)
                elif text.startswith("## "):
                    doc.add_heading(text[3:].strip(), level=2)
                elif text.startswith("# "):
                    doc.add_heading(text[2:].strip(), level=1)
                else:
                    doc.add_paragraph(text)

        # 元数据附录
        doc.add_page_break()
        doc.add_heading("文档信息", level=1)
        doc.add_paragraph(f"材料类型: {spec.name}")
        doc.add_paragraph(f"总字数: {draft.total_words()}")
        doc.add_paragraph(f"范式: {spec.paradigm}")
        doc.add_paragraph(f"参考材料模式: {spec.material_mode}")
        if draft.meta.get("duration"):
            doc.add_paragraph(f"耗时: {draft.meta['duration']:.1f} 秒")

        doc.add_heading("章节字数", level=2)
        for s in draft.sections:
            doc.add_paragraph(f"{s.title}: {s.word_count} 字")

        doc.save(str(path))
