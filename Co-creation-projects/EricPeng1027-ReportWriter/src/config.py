"""配置管理模块

统一从 .env 读取配置，与 HelloAgents 框架的统一变量约定保持一致：
LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL / LLM_TIMEOUT，
以及 RAG 所需的 QDRANT_* 与 EMBED_* 配置。
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# 加载环境变量（项目根目录下的 .env）
_BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BASE_DIR / ".env")


class Settings(BaseSettings):
    """应用配置

    遵循 hello-agents 框架的统一配置约定，参数优先、环境变量兜底。
    """

    # ---- LLM 配置（与框架统一变量对齐）----
    llm_model_id: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_timeout: int = 180

    # ---- RAG / 向量库配置 ----
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "reportwriter_vectors"
    qdrant_vector_size: int = 1024  # dashscope text-embedding-v3 为 1024
    qdrant_distance: str = "cosine"
    qdrant_timeout: int = 30

    # ---- Embedding 配置 ----
    embed_model_type: str = "dashscope"
    embed_model_name: str = ""  # 为空时 dashscope 默认 text-embedding-v3
    embed_api_key: str = ""
    embed_base_url: str = ""

    # ---- 系统配置 ----
    # 评审通过阈值（分数 >= 此值则通过）
    approval_threshold: int = 75
    # 修改阈值（分数 < 此值则需要重写）
    revision_threshold: int = 60
    # 最大修改轮次
    max_revisions: int = 2
    # 是否启用评审
    enable_review: bool = True

    # ---- 路径配置 ----
    data_dir: str = str(_BASE_DIR / "data")
    output_dir: str = str(_BASE_DIR / "outputs")
    material_kb_dir: str = str(_BASE_DIR / ".material_kb")

    # 字数误差容忍度
    word_count_tolerance: float = 0.1

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # 忽略 .env 中未使用的字段，避免校验报错


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取配置实例（单例模式）"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_word_count_tolerance() -> float:
    """获取字数误差容忍度"""
    return get_settings().word_count_tolerance
