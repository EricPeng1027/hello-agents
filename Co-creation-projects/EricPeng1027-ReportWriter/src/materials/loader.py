"""文件加载器：扫描目录、识别可导入的参考材料文件"""

import os
from pathlib import Path
from typing import List

# RAGTool 经 MarkItDown 支持的格式
SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx", ".html", ".htm", ".json"}


def scan_materials(data_dir: str) -> List[Path]:
    """扫描目录下所有受支持的参考材料文件

    Args:
        data_dir: 参考材料目录

    Returns:
        受支持文件的 Path 列表
    """
    root = Path(data_dir)
    if not root.exists():
        print(f"▸ 参考材料目录不存在: {data_dir}")
        return []

    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)

    print(f"▸ 在 {data_dir} 下找到 {len(files)} 个参考材料文件")
    return files


def is_supported(path: str) -> bool:
    """判断文件是否受支持"""
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS
