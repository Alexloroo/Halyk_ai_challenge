import pytest

from halyk_covenants.config import DeepSeekSettings
from halyk_covenants.llm import DeepSeekChatFactory, DeepSeekConfigurationError


def test_deepseek_factory_fails_cleanly_without_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(DeepSeekConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekChatFactory(DeepSeekSettings()).create()


def test_deepseek_factory_builds_langchain_model_without_exposing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeChatDeepSeek:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-test-key")
    monkeypatch.setattr("halyk_covenants.llm.client.ChatDeepSeek", FakeChatDeepSeek)

    model = DeepSeekChatFactory(DeepSeekSettings(model="deepseek-v4-pro")).create()

    assert isinstance(model, FakeChatDeepSeek)
    assert captured["model"] == "deepseek-v4-pro"
    assert captured["api_key"] == "secret-test-key"
