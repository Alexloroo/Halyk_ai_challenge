from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from halyk_covenants.config import DeepSeekSettings
from halyk_covenants.observability import trace_stage


class DeepSeekConfigurationError(RuntimeError):
    pass


class DeepSeekChatFactory:
    def __init__(self, settings: DeepSeekSettings) -> None:
        self.settings = settings

    @trace_stage("llm.deepseek.create", run_type="tool", tags=("preprocessing", "llm"))
    def create(self) -> BaseChatModel:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise DeepSeekConfigurationError(
                "DEEPSEEK_API_KEY is required to initialize the DeepSeek LangChain client"
            )
        return ChatDeepSeek(
            model=self.settings.model,
            api_key=api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_seconds,
            max_retries=self.settings.max_retries,
            temperature=0,
        )
