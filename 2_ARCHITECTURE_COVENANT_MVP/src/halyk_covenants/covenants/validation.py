from __future__ import annotations

import re

from halyk_covenants.domain import CovenantSpec, MetricSpec
from halyk_covenants.domain.transaction_fields import FILTER_FIELDS, PHYSICAL_TRANSACTION_FIELDS

_CURRENCY = re.compile(
    r"(?:\bKZT\b|\bUSD\b|\bEUR\b|\bRUB\b|тенге|доллар|евро|рубл)",
    flags=re.IGNORECASE,
)
_OUTGOING = re.compile(r"\b(?:outgoing|исходящ\w*)\b", flags=re.IGNORECASE)
_INCOMING = re.compile(r"\b(?:incoming|входящ\w*|пополн\w*)\b", flags=re.IGNORECASE)


def _metric_errors(metric: MetricSpec, path: str = "metric") -> list[str]:
    errors: list[str] = []
    if metric.field is not None and metric.field not in PHYSICAL_TRANSACTION_FIELDS:
        errors.append(f"{path}.field unsupported: {metric.field}")
    for index, filter_spec in enumerate([*metric.filters, *metric.exclusions]):
        if filter_spec.field not in FILTER_FIELDS:
            errors.append(f"{path}.filter[{index}].field unsupported: {filter_spec.field}")
    if metric.numerator is not None:
        errors.extend(_metric_errors(metric.numerator, f"{path}.numerator"))
    if metric.denominator is not None:
        errors.extend(_metric_errors(metric.denominator, f"{path}.denominator"))
    return errors


def validate_compiled_spec(
    spec: CovenantSpec,
    *,
    clause: str,
    allowed_borrower_ids: list[str],
) -> list[str]:
    errors = _metric_errors(spec.metric)
    for index, filter_spec in enumerate([*spec.transaction_filters, *spec.exclusions]):
        if filter_spec.field not in FILTER_FIELDS:
            errors.append(f"filter[{index}].field unsupported: {filter_spec.field}")

    monetary_output = spec.metric.field == "amount" and spec.metric.metric_type in {
        "sum",
        "max",
        "min",
        "avg",
    }
    if _CURRENCY.search(clause) and monetary_output and not spec.condition.currency:
        errors.append("currency is explicit in the clause but absent from condition")

    filter_pairs = {
        (filter_spec.field, filter_spec.operator, str(filter_spec.value).casefold())
        for filter_spec in [*spec.transaction_filters, *spec.metric.filters]
    }
    if _OUTGOING.search(clause) and ("direction", "eq", "outgoing") not in filter_pairs:
        errors.append("outgoing clause requires transaction filter direction=outgoing")
    if _INCOMING.search(clause) and ("direction", "eq", "incoming") not in filter_pairs:
        errors.append("incoming clause requires transaction filter direction=incoming")
    currency_match = _CURRENCY.search(clause)
    if currency_match and monetary_output:
        currency = currency_match.group(0).upper()
        if (
            currency in {"KZT", "USD", "EUR", "RUB"}
            and ("currency", "eq", currency.casefold()) not in filter_pairs
        ):
            errors.append(f"monetary clause requires transaction filter currency={currency}")

    if allowed_borrower_ids and not set(spec.borrower_ids).issubset(allowed_borrower_ids):
        errors.append("compiled borrower_ids contain identifiers absent from resolved scope")
    if not spec.borrower_ids:
        errors.append("borrower scope is unresolved")

    if spec.effective_from and spec.effective_to and spec.effective_from > spec.effective_to:
        errors.append("effective_from cannot be after effective_to")
    return errors
