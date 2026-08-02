from datetime import date, timedelta
from typing import Any

from halyk_covenants.domain import FilterSpec, TimeWindowSpec
from halyk_covenants.sql.filters import compile_filter


def build_where_clause(
    borrower_id: str | list[str],
    filters: list[FilterSpec],
    *,
    exclusions: list[FilterSpec] | None = None,
    date_field: str = "transaction_date",
    time_window: TimeWindowSpec | None = None,
    evaluation_date: date | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
) -> tuple[str, list[Any]]:
    """Build a borrower-scoped, parameterized WHERE clause.

    Metric windows and covenant effective dates are intersected at query time. This keeps even a
    single covenant version from consuming transactions that occurred before it became effective or
    after it expired.
    """
    single_borrower = isinstance(borrower_id, str)
    borrower_ids = [borrower_id] if single_borrower else borrower_id
    if not borrower_ids:
        raise ValueError("at least one borrower_id is required")
    placeholders = ", ".join("?" for _ in borrower_ids)
    borrower_predicate = (
        "borrower_id = ?" if single_borrower else f"borrower_id IN ({placeholders})"
    )
    predicates = [borrower_predicate]
    parameters: list[Any] = list(borrower_ids)

    for filter_spec in filters:
        predicate, filter_parameters = compile_filter(filter_spec)
        predicates.append(predicate)
        parameters.extend(filter_parameters)

    for exclusion in exclusions or []:
        predicate, filter_parameters = compile_filter(exclusion)
        predicates.append(f"COALESCE(NOT ({predicate}), TRUE)")
        parameters.extend(filter_parameters)

    bounds = window_bounds(time_window, evaluation_date)
    if bounds is not None:
        start, end = bounds
        predicates.extend([f"{date_field} >= ?", f"{date_field} < ?"])
        parameters.extend([start, end])
    elif evaluation_date is not None:
        predicates.append(f"{date_field} < ?")
        parameters.append(evaluation_date + timedelta(days=1))

    if effective_from is not None:
        predicates.append(f"{date_field} >= ?")
        parameters.append(effective_from)
    if effective_to is not None:
        predicates.append(f"{date_field} < ?")
        parameters.append(effective_to + timedelta(days=1))

    return f"WHERE {' AND '.join(predicates)}", parameters


def window_bounds(
    window: TimeWindowSpec | None,
    evaluation_date: date | None,
) -> tuple[date, date] | None:
    """Return the half-open [start, end) interval used by deterministic SQL evaluation."""
    if window is None or window.type == "none":
        return None
    if window.type == "custom":
        assert window.start_date is not None and window.end_date is not None
        return window.start_date, window.end_date + timedelta(days=1)
    if evaluation_date is None:
        raise ValueError(f"evaluation_date is required for {window.type} time windows")

    if window.type == "calendar_day":
        return evaluation_date, evaluation_date + timedelta(days=1)
    if window.type == "calendar_week":
        start = evaluation_date - timedelta(days=evaluation_date.weekday())
        return start, start + timedelta(days=7)
    if window.type == "calendar_month":
        start = evaluation_date.replace(day=1)
        return start, _first_day_of_next_month(start)
    if window.type == "calendar_quarter":
        first_month = ((evaluation_date.month - 1) // 3) * 3 + 1
        start = date(evaluation_date.year, first_month, 1)
        if first_month == 10:
            return start, date(evaluation_date.year + 1, 1, 1)
        return start, date(evaluation_date.year, first_month + 3, 1)
    if window.type == "calendar_year":
        return date(evaluation_date.year, 1, 1), date(evaluation_date.year + 1, 1, 1)
    if window.type == "rolling_days":
        assert window.rolling_days is not None
        return (
            evaluation_date - timedelta(days=window.rolling_days - 1),
            evaluation_date + timedelta(days=1),
        )
    raise ValueError(f"Unsupported time window: {window.type}")


def _first_day_of_next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
