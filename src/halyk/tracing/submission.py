"""Trace the final submission payload."""

from __future__ import annotations

from .writer import TraceWriter


def trace_submission(writer: TraceWriter, submission: dict[str, object]) -> None:
    writer.write_json("13_submission", "submission.json", submission)
    answers = submission.get("answers")
    scenarios = len(answers) if isinstance(answers, dict) else 0
    cells = (
        sum(len(clauses) for clauses in answers.values() if isinstance(clauses, dict))
        if isinstance(answers, dict)
        else 0
    )
    writer.update_stage(
        "13_submission", status="completed", scenarios=scenarios, cells=cells
    )
