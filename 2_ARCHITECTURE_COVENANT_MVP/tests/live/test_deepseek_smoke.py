import os

import pytest

from halyk_covenants.config import DeepSeekSettings
from halyk_covenants.llm import DeepSeekChatFactory


@pytest.mark.skipif(os.getenv("RUN_DEEPSEEK_LIVE") != "1", reason="opt-in DeepSeek live test")
def test_deepseek_langchain_live_smoke() -> None:
    response = DeepSeekChatFactory(DeepSeekSettings()).create().invoke("Reply with the word OK.")
    assert response.content
