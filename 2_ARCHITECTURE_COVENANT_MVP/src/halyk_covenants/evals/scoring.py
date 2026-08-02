from __future__ import annotations

from decimal import Decimal
from typing import Any

from halyk_covenants.domain import CovenantResult, CovenantSpec


def _number_equal(expected: Decimal | int | None, actual: Decimal | int | None) -> bool:
    if expected is None or actual is None:
        return expected is actual
    return Decimal(str(expected)) == Decimal(str(actual))


def score_covenant_result(
    expected: CovenantResult,
    actual: CovenantResult,
) -> dict[str, int]:
    """Score the three independently valuable hackathon answer components."""
    number = int(_number_equal(expected.number, actual.number))
    verdict = int(expected.verdict == actual.verdict)
    evidence = int(expected.evidence_transaction_id == actual.evidence_transaction_id)
    total = number + verdict + evidence
    return {
        "number_exact": number,
        "verdict_exact": verdict,
        "evidence_exact": evidence,
        "component_score": total,
        "full_exact_match": int(total == 3),
    }


def _filters(spec: CovenantSpec) -> list[dict[str, Any]]:
    payload = [item.model_dump(mode="json") for item in spec.transaction_filters]
    return sorted(payload, key=lambda item: (item["field"], item["operator"], repr(item["value"])))


def _threshold_equal(expected: CovenantSpec, actual: CovenantSpec) -> bool:
    left = expected.condition.threshold
    right = actual.condition.threshold
    if left is None or right is None:
        return left is right
    return Decimal(str(left)) == Decimal(str(right))


def score_compiler_output(
    expected: CovenantSpec,
    actual: CovenantSpec,
) -> dict[str, int]:
    """Expose compiler correctness per field instead of hiding failures in one score."""
    expected_window = expected.time_window.model_dump(mode="json") if expected.time_window else None
    actual_window = actual.time_window.model_dump(mode="json") if actual.time_window else None
    return {
        "metric_type_exact": int(expected.metric.metric_type == actual.metric.metric_type),
        "field_exact": int(expected.metric.field == actual.metric.field),
        "filters_exact": int(_filters(expected) == _filters(actual)),
        "period_exact": int(
            expected_window == actual_window and expected.date_field == actual.date_field
        ),
        "comparator_exact": int(expected.condition.comparator == actual.condition.comparator),
        "threshold_exact": int(_threshold_equal(expected, actual)),
        "currency_exact": int(expected.condition.currency == actual.condition.currency),
        "borrower_scope_exact": int(
            expected.scope_mode == actual.scope_mode
            and set(expected.borrower_ids) == set(actual.borrower_ids)
        ),
        "evidence_mode_exact": int(expected.evidence_mode == actual.evidence_mode),
    }


def score_covenant_detection(
    expected_ids: list[str] | tuple[str, ...] | set[str],
    actual_ids: list[str] | tuple[str, ...] | set[str],
) -> dict[str, int | Decimal]:
    """Measure covenant discovery with recall as a first-class metric."""
    expected = set(expected_ids)
    actual = set(actual_ids)
    true_positive = len(expected & actual)
    false_negative = len(expected - actual)
    false_positive = len(actual - expected)
    recall_denominator = len(expected)
    precision_denominator = len(actual)
    recall = (
        Decimal(true_positive) / Decimal(recall_denominator)
        if recall_denominator
        else Decimal(1 if not actual else 0)
    )
    precision = (
        Decimal(true_positive) / Decimal(precision_denominator)
        if precision_denominator
        else Decimal(1 if not expected else 0)
    )
    return {
        "true_positive": true_positive,
        "false_negative": false_negative,
        "false_positive": false_positive,
        "recall": recall,
        "precision": precision,
    }
