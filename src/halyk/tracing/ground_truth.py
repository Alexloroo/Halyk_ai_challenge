"""Compare the final submission with optional local ground truth."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .writer import TraceWriter

STAGE = "14_ground_truth"
FIVE_PERCENT = Decimal("0.05")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _expected_cells(ground_truth: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    scenarios = ground_truth.get("scenarios")
    if not isinstance(scenarios, dict):
        return cells
    for scenario_id, scenario in scenarios.items():
        covenants = scenario.get("covenants") if isinstance(scenario, dict) else None
        if not isinstance(covenants, dict):
            continue
        for clause, expected in covenants.items():
            if isinstance(expected, dict):
                cells[(str(scenario_id), str(clause))] = expected
    return cells


def _submitted_cells(submission: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    answers = submission.get("answers")
    if not isinstance(answers, dict):
        return cells
    for scenario_id, clauses in answers.items():
        if not isinstance(clauses, dict):
            continue
        for clause, actual in clauses.items():
            if isinstance(actual, dict):
                cells[(str(scenario_id), str(clause))] = actual
    return cells


def _compare_cell(
    scenario_id: str,
    clause: str,
    expected: dict[str, Any] | None,
    submitted: dict[str, Any] | None,
) -> dict[str, Any]:
    expected_present = expected is not None
    submitted_present = submitted is not None
    expected = expected or {}
    submitted = submitted or {}
    expected_actual = _decimal(expected.get("actual"))
    submitted_actual = _decimal(submitted.get("actual"))
    actual_exact = expected_actual is not None and submitted_actual == expected_actual

    absolute_error: Decimal | None = None
    relative_error: Decimal | None = None
    factor = Decimal(0)
    if expected_actual is not None and submitted_actual is not None:
        absolute_error = abs(submitted_actual - expected_actual)
        if expected_actual == 0:
            factor = Decimal(1) if absolute_error == 0 else Decimal(0)
            relative_error = Decimal(0) if absolute_error == 0 else None
        else:
            relative_error = absolute_error / abs(expected_actual)
            factor = max(Decimal(0), Decimal(1) - relative_error / FIVE_PERCENT)

    expected_status = expected.get("status")
    submitted_status = submitted.get("status")
    status_match = expected_status is not None and submitted_status == expected_status
    expected_evidence = expected.get("evidence_txn_id")
    submitted_evidence = submitted.get("evidence_txn_id")
    evidence_match = (
        expected_present
        and submitted_present
        and submitted_evidence == expected_evidence
    )

    cell_score = Decimal(0)
    if status_match:
        cell_score = Decimal("0.50") + Decimal("0.30") * factor
        if expected_evidence is None:
            cell_score += Decimal("0.20") * factor
        elif evidence_match:
            cell_score += Decimal("0.20")

    return {
        "scenario_id": scenario_id,
        "clause": clause,
        "cell_presence": (
            "both"
            if expected_present and submitted_present
            else "missing_submission"
            if expected_present
            else "unexpected"
        ),
        "expected_status": expected_status,
        "submitted_status": submitted_status,
        "status_match": status_match,
        "expected_actual": float(expected_actual) if expected_actual is not None else None,
        "submitted_actual": float(submitted_actual) if submitted_actual is not None else None,
        "actual_exact_match": actual_exact,
        "actual_absolute_error": float(absolute_error) if absolute_error is not None else None,
        "actual_relative_error": (
            float(relative_error.quantize(Decimal("0.0000000001")))
            if relative_error is not None
            else None
        ),
        "actual_score_factor": float(factor.quantize(Decimal("0.0000000001"))),
        "actual_within_5_percent": relative_error is not None and relative_error < FIVE_PERCENT,
        "expected_evidence_txn_id": expected_evidence,
        "submitted_evidence_txn_id": submitted_evidence,
        "evidence_match": evidence_match,
        "exact_cell_match": status_match and actual_exact and evidence_match,
        "cell_score": float(cell_score.quantize(Decimal("0.000001"))),
    }


def compare_ground_truth(
    submission: dict[str, Any],
    ground_truth: dict[str, Any],
) -> dict[str, Any]:
    expected = _expected_cells(ground_truth)
    submitted = _submitted_cells(submission)
    keys = list(expected)
    keys.extend(sorted(key for key in submitted if key not in expected))
    comparisons = [
        _compare_cell(
            scenario,
            clause,
            expected.get((scenario, clause)),
            submitted.get((scenario, clause)),
        )
        for scenario, clause in keys
    ]
    score = sum((Decimal(str(row["cell_score"])) for row in comparisons), Decimal(0))
    maximum = Decimal(len(comparisons))
    percent = score / maximum * Decimal(100) if maximum else Decimal(0)
    summary = {
        "status": "compared",
        "cells": len(comparisons),
        "status_matches": sum(bool(row["status_match"]) for row in comparisons),
        "actual_exact_matches": sum(bool(row["actual_exact_match"]) for row in comparisons),
        "actual_within_5_percent": sum(
            bool(row["actual_within_5_percent"]) for row in comparisons
        ),
        "evidence_matches": sum(bool(row["evidence_match"]) for row in comparisons),
        "exact_cell_matches": sum(bool(row["exact_cell_match"]) for row in comparisons),
        "unweighted_score": float(score.quantize(Decimal("0.000001"))),
        "unweighted_max": float(maximum),
        "unweighted_percent": float(percent.quantize(Decimal("0.0001"))),
    }
    return {"summary": summary, "comparisons": comparisons}


def trace_ground_truth(
    writer: TraceWriter,
    submission: dict[str, Any],
    ground_truth_path: Path,
) -> dict[str, Any]:
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    result = compare_ground_truth(submission, ground_truth)
    payload = {"ground_truth_path": ground_truth_path, **result}
    writer.write_json(STAGE, "comparison.json", payload)
    writer.write_csv(STAGE, "comparison.csv", result["comparisons"])
    summary = result["summary"]
    writer.update_stage(
        STAGE,
        status="completed",
        cells=summary["cells"],
        exact_cell_matches=summary["exact_cell_matches"],
        unweighted_score=summary["unweighted_score"],
        unweighted_max=summary["unweighted_max"],
        unweighted_percent=summary["unweighted_percent"],
    )
    return result
