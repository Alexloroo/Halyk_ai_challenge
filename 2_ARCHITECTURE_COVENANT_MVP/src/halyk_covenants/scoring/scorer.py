"""Score a submission against the answer key, exactly as the case specifies.

    status            0.50   exact match; wrong status zeroes the whole cell
    actual            0.30 x max(0, 1 - e/0.05),  e = |ours - key| / |key|
    evidence_txn_id   0.20   exact match; when the key is null these points
                             decay with actual on the same scale

Reference: data/raw/CASE.ru.md section 4.
"""

from __future__ import annotations

from typing import Any

from .models import (
    ACTUAL_TOLERANCE,
    ACTUAL_WEIGHT,
    EVIDENCE_WEIGHT,
    STATUS_WEIGHT,
    CellScore,
    ScoreReport,
)

VALID_STATUS = {"COMPLIANT", "BREACH"}


def _as_number(value: Any) -> float | None:
    """Numbers only. A numeric string is not a number for scoring purposes."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _actual_fraction(ours: Any, key: Any) -> tuple[float, str]:
    """Return (fraction of ACTUAL_WEIGHT earned, reason)."""
    key_value = _as_number(key)
    if key_value is None:
        # No numeric key to compare against; nothing can be earned or lost.
        return 1.0, "actual: no numeric key"

    our_value = _as_number(ours)
    if our_value is None:
        return 0.0, "actual: missing or non-numeric"

    if key_value == 0:
        return (1.0, "actual: exact") if our_value == 0 else (0.0, "actual: key is zero")

    error = abs(our_value - key_value) / abs(key_value)
    fraction = max(0.0, 1.0 - error / ACTUAL_TOLERANCE)
    return fraction, f"actual: relative error {error:.4f}"


def score_cell(
    scenario_id: str,
    clause: str,
    answer: dict[str, Any] | None,
    key: dict[str, Any],
) -> CellScore:
    zero = CellScore(
        scenario_id=scenario_id,
        clause=clause,
        status_points=0.0,
        actual_points=0.0,
        evidence_points=0.0,
    )

    if answer is None:
        return zero.model_copy(update={"reason": "cell absent"})

    status = answer.get("status")
    if status not in VALID_STATUS:
        return zero.model_copy(update={"reason": f"status not in {sorted(VALID_STATUS)}"})
    if status != key.get("status"):
        return zero.model_copy(update={"reason": f"status {status} != key {key.get('status')}"})

    fraction, actual_reason = _actual_fraction(answer.get("actual"), key.get("actual"))
    actual_points = ACTUAL_WEIGHT * fraction

    key_evidence = key.get("evidence_txn_id")
    if key_evidence is None:
        # Nothing to identify; the points ride on actual instead.
        evidence_points = EVIDENCE_WEIGHT * fraction
        evidence_reason = "evidence: key null, decays with actual"
    else:
        exact = answer.get("evidence_txn_id") == key_evidence
        evidence_points = EVIDENCE_WEIGHT if exact else 0.0
        evidence_reason = f"evidence: {'exact' if exact else 'mismatch'}"

    return CellScore(
        scenario_id=scenario_id,
        clause=clause,
        status_points=STATUS_WEIGHT,
        actual_points=actual_points,
        evidence_points=evidence_points,
        reason=f"{actual_reason} | {evidence_reason}",
    )


def score_submission(
    submission: dict[str, Any],
    ground_truth: dict[str, Any],
) -> ScoreReport:
    """Score a submission payload against a ground-truth payload.

    The key defines which cells exist. A cell present in the submission but not
    in the key earns nothing and is reported separately; a cell in the key with
    no answer scores zero.
    """
    answers = submission.get("answers") or {}
    scenarios = ground_truth.get("scenarios") or {}

    cells: list[CellScore] = []
    missing: list[str] = []

    for scenario_id, scenario in scenarios.items():
        for clause, key in (scenario.get("covenants") or {}).items():
            answer = (answers.get(scenario_id) or {}).get(clause)
            if answer is None:
                missing.append(f"{scenario_id}/{clause}")
            cells.append(score_cell(scenario_id, clause, answer, key))

    expected = {
        (s, c) for s, sc in scenarios.items() for c in (sc.get("covenants") or {})
    }
    extra = [
        f"{s}/{c}"
        for s, sc in answers.items()
        if isinstance(sc, dict)
        for c in sc
        if (s, c) not in expected
    ]

    return ScoreReport(cells=cells, missing_cells=missing, extra_cells=sorted(extra))
