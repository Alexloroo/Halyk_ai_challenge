"""Trace each covenant calculation and counterfactual evidence trial."""

from __future__ import annotations

from halyk.evaluate import Answer, EvaluationTrace
from halyk.generic_formula import ExternalMetric, GenericFormulaSpec
from halyk.ledger import LedgerEntry
from halyk.llm_extract import FormulaSpec
from halyk.llm_full_context import FullContextResult
from halyk.rules import Rule

from .ledger import entry_row
from .writer import TraceWriter


def trace_evaluation(
    writer: TraceWriter,
    scenario_id: str,
    clause: str,
    *,
    rule: Rule | None,
    formula: FormulaSpec | None,
    entries: list[LedgerEntry],
    details: EvaluationTrace | None,
    answer: Answer,
    evidence_trials: dict[str, str],
    generic_formula: GenericFormulaSpec | None = None,
    external_metrics: dict[str, ExternalMetric] | None = None,
    documentary_fact: object = None,
    full_context_result: FullContextResult | None = None,
) -> None:
    writer.write_json(
        "12_evaluation",
        f"{scenario_id}/{clause.replace('.', '_')}.json",
        {
            "scenario_id": scenario_id,
            "clause": clause,
            "rule": rule,
            "formula": formula,
            "generic_formula": generic_formula,
            "external_metrics": external_metrics or {},
            "documentary_fact": documentary_fact,
            "full_context_result": full_context_result,
            "scenario_entries": [entry_row(entry) for entry in entries],
            "calculation": details,
            "answer": answer,
            "evidence_trials": evidence_trials,
        },
    )
