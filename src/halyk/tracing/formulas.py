"""Trace LLM formula inputs and structured outputs without credentials."""

from __future__ import annotations

from halyk.llm_extract import SYSTEM_PROMPT, FormulaSpec
from halyk.rules import Rule

from .writer import TraceWriter


def trace_formulas(
    writer: TraceWriter,
    rules: dict[str, dict[str, Rule]],
    formulas: dict[str, FormulaSpec],
    *,
    enabled: bool,
) -> None:
    writer.write_text("11_formulas", "system_prompt.txt", SYSTEM_PROMPT)
    records: dict[str, object] = {}
    for scenario_id, clauses in rules.items():
        for clause_id, rule in clauses.items():
            key = f"{scenario_id}/{clause_id}"
            records[key] = {
                "llm_enabled": enabled,
                "rule": rule,
                "formula": formulas.get(key),
            }
    writer.write_json("11_formulas", "formulas.json", records)
    writer.update_stage("11_formulas", formulas=len(formulas), status="completed")
