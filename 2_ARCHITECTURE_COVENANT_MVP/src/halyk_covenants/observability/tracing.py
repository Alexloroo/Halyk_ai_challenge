from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from langsmith import traceable as langsmith_traceable

from halyk_covenants.domain.failure import FailureStage
from halyk_covenants.observability.context import (
    annotate_current_trace,
    current_trace_metadata,
)

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
    failure_stage: FailureStage | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Trace one meaningful business stage.

    Static metadata is attached at decoration time; run/case/borrower/covenant metadata is added
    dynamically from :func:`trace_context` when the function executes. If the wrapped business
    function raises, the current span receives a stable failure-stage label before the exception
    propagates. Input/output redaction happens before remote serialization.
    """
    input_fields = frozenset(redact_inputs)
    output_fields = frozenset(redact_outputs)

    def process_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
        return _redact_mapping(inputs, input_fields)

    def process_outputs(output: Any) -> Any:
        return _redact_mapping(output, output_fields)

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def instrumented(*args: P.args, **kwargs: P.kwargs) -> R:
            inherited = current_trace_metadata()
            if inherited:
                annotate_current_trace(metadata=inherited)
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                failure_metadata: dict[str, Any] = {
                    "error_type": type(exc).__name__,
                }
                if failure_stage is not None:
                    failure_metadata["failure_stage"] = failure_stage.value
                annotate_current_trace(metadata=failure_metadata, tags=("failed",))
                raise

        return langsmith_traceable(
            name=name,
            run_type=run_type,
            tags=list(tags),
            metadata=metadata or {},
            process_inputs=process_inputs,
            process_outputs=process_outputs,
        )(instrumented)

    return decorate
