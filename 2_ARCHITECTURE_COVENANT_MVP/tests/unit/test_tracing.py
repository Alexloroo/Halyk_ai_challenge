from __future__ import annotations

from collections.abc import Callable
from typing import Any

from halyk_covenants.observability.tracing import trace_stage


def test_trace_stage_preserves_behavior_when_remote_tracing_is_disabled(monkeypatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)

    @trace_stage("transaction.ingest", run_type="tool", redact_inputs={"rows"})
    def ingest(rows: list[dict[str, str]]) -> int:
        return len(rows)

    assert ingest([{"account": "secret"}]) == 1
    assert ingest.__name__ == "ingest"


def test_trace_stage_passes_redacted_processors_to_traceable(monkeypatch) -> None:
    recorded: dict[str, Any] = {}

    def fake_traceable(**kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        recorded.update(kwargs)

        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            return function

        return decorate

    monkeypatch.setattr("halyk_covenants.observability.tracing.langsmith_traceable", fake_traceable)

    @trace_stage(
        "llm.deepseek",
        run_type="llm",
        redact_inputs={"api_key", "raw_pdf"},
        redact_outputs={"reasoning_content"},
    )
    def invoke(api_key: str, raw_pdf: bytes) -> dict[str, str]:
        return {"content": "{}", "reasoning_content": "private"}

    invoke("secret", b"pdf")
    safe_inputs = recorded["process_inputs"]({"api_key": "secret", "raw_pdf": b"pdf"})
    safe_output = recorded["process_outputs"]({"content": "{}", "reasoning_content": "private"})

    assert safe_inputs == {"api_key": "[REDACTED]", "raw_pdf": "[REDACTED]"}
    assert safe_output == {"content": "{}", "reasoning_content": "[REDACTED]"}
    assert recorded["name"] == "llm.deepseek"
    assert recorded["run_type"] == "llm"
