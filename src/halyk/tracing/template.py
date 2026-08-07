"""Trace submission-template loading."""

from __future__ import annotations

from .writer import TraceWriter

STAGE = "01_template"


def trace_template(writer: TraceWriter, template: dict[str, list[str]]) -> None:
    writer.write_json(STAGE, "template.json", template)
    writer.update_stage(
        STAGE,
        status="completed",
        scenarios=len(template),
        cells=sum(len(clauses) for clauses in template.values()),
    )
