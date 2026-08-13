"""LLM 服务单例

封装 HelloAgentsLLM，全局复用一个客户端实例，
自动从 .env 读取 LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL。
"""

from typing import Optional

from hello_agents import HelloAgentsLLM

from ..config import get_settings


class LLMService:
    """LLM 服务单例"""

    _instance: Optional[HelloAgentsLLM] = None

    @classmethod
    def get_llm(cls) -> HelloAgentsLLM:
        """获取 LLM 实例（单例模式）"""
        if cls._instance is None:
            settings = get_settings()
            cls._instance = HelloAgentsLLM(
                model=settings.llm_model_id or None,
                api_key=settings.llm_api_key or None,
                base_url=settings.llm_base_url or None,
                timeout=settings.llm_timeout,
            )
            print(f"▸ LLM 服务初始化成功")
            print(f"   模型: {cls._instance.model}")
            print(f"   服务地址: {cls._instance.base_url}")
        return cls._instance
