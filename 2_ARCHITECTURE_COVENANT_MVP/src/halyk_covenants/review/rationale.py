from __future__ import annotations

from halyk_covenants.review.models import ReviewCase


def build_rationale(case: ReviewCase) -> str:
    covenant = case.covenant
    answer = case.answer
    calculation = case.calculation

    parts: list[str] = [
        f"Covenant {case.covenant_id} for borrower {case.borrower_id}.",
        f"Metric={covenant.metric.metric_type}.",
    ]
    if covenant.time_window is not None:
        parts.append(f"Window={covenant.time_window.type}.")
    if covenant.effective_from is not None or covenant.effective_to is not None:
        parts.append(
            "Effective="
            f"{covenant.effective_from.isoformat() if covenant.effective_from else '-inf'}"
            ".."
            f"{covenant.effective_to.isoformat() if covenant.effective_to else '+inf'}."
        )

    if calculation is not None:
        unit = f" {calculation.unit}" if calculation.unit else ""
        parts.append(
            f"Calculated value={calculation.value}{unit} from "
            f"{calculation.input_row_count} matched transactions."
        )
    elif answer.number is not None:
        unit = f" {answer.number_unit}" if answer.number_unit else ""
        parts.append(f"Calculated value={answer.number}{unit}.")
    else:
        parts.append("Calculated value is unavailable.")

    threshold = covenant.condition.threshold
    if threshold is not None:
        unit = covenant.condition.currency or covenant.condition.unit or ""
        suffix = f" {unit}" if unit else ""
        parts.append(
            f"Rule requires value {covenant.condition.comparator} {threshold}{suffix}."
        )
    parts.append(f"Deterministic verdict={answer.verdict}.")

    if answer.evidence_transaction_id is not None:
        parts.append(f"Evidence transaction={answer.evidence_transaction_id}.")
    elif answer.verdict == "violated":
        parts.append("No single evidence transaction is attached to this deterministic result.")

    if case.verification_issues:
        parts.append("Verification issues: " + "; ".join(case.verification_issues) + ".")

    return " ".join(parts)
