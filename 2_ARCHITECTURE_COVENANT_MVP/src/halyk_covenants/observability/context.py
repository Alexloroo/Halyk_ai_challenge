from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import langsmith as ls

_TRACE_METADATA: ContextVar[dict[str, Any]] = ContextVar(
    "halyk_trace_metadata",
    default={},
)


def current_trace_metadata() -> dict[str, Any]:
    """Return a defensive copy of metadata inherited by the current pipeline scope."""
    return dict(_TRACE_METADATA.get())


@contextmanager
def trace_context(**metadata: Any) -> Iterator[dict[str, Any]]:
    """Merge metadata for child spans without creating an additional business span.

    The local ContextVar is always available, even with LangSmith tracing disabled. When
    LangSmith is enabled, its tracing_context propagates the same metadata to child spans.
    """
    merged = current_trace_metadata()
    merged.update({key: value for key, value in metadata.items() if value is not None})
    token = _TRACE_METADATA.set(merged)
    try:
        with ls.tracing_context(metadata=merged):
            yield dict(merged)
    finally:
        _TRACE_METADATA.reset(token)


def annotate_current_trace(
    *,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | tuple[str, ...] = (),
) -> None:
    """Best-effort annotation of the currently active LangSmith span.

    Observability must never become a correctness dependency, so missing trace state or SDK
    annotation failures are intentionally ignored.
    """
    try:
        run = ls.get_current_run_tree()
        if run is None:
            return
        if metadata:
            if hasattr(run, "add_metadata"):
                run.add_metadata(metadata)
            else:
                run.metadata.update(metadata)
        if tags:
            run.tags.extend(tag for tag in tags if tag not in run.tags)
    except Exception:
        return
