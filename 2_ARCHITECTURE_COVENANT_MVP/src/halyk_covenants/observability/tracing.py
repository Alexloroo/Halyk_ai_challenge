from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, ParamSpec, TypeVar

from langsmith import traceable as langsmith_traceable

P = ParamSpec("P")
R = TypeVar("R")

REDACTED = "[REDACTED]"


def _redact_mapping(payload: Any, fields: frozenset[str]) -> Any:
    if not isinstance(payload, dict):
        return payload
    return {key: REDACTED if key in fields else value for key, value in payload.items()}


def trace_stage(
    name: str,
    *,
    run_type: str = "chain",
    tags: Iterable[str] = (),
    metadata: dict[str, Any] | None = None,
    redact_inputs: Iterable[str] = (),
    redact_outputs: Iterable[str] = (),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Trace a stage while redacting declared mapping keys before remote serialization."""
    input_fields = frozenset(redact_inputs)
    output_fields = frozenset(redact_outputs)

    def process_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
        return _redact_mapping(inputs, input_fields)

    def process_outputs(output: Any) -> Any:
        return _redact_mapping(output, output_fields)

    return langsmith_traceable(
        name=name,
        run_type=run_type,
        tags=list(tags),
        metadata=metadata or {},
        process_inputs=process_inputs,
        process_outputs=process_outputs,
    )
