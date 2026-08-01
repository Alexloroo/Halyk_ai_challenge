from datetime import date, timedelta
from typing import Any

from halyk_covenants.domain import FilterSpec, TimeWindowSpec
from halyk_covenants.sql.filters import compile_filter


def build_where_clause(
    borrower_id: str,
    filters: list[FilterSpec],
    *,
    time_window: TimeWindowSpec | None = None,
    evaluation_date: date | None = None,
) -> tuple[str, list[Any]]:
    """Build a borrower-scoped, parameterized WHERE clause."""
    predicates = ["borrower_id = ?"]
    parameters: list[Any] = [borrower_id]

    for filter_spec in filters:
        predicate, filter_parameters = compile_filter(filter_spec)
        predicates.append(predicate)
        parameters.extend(filter_parameters)

    bounds = _window_bounds(time_window, evaluation_date)
    if bounds is not None:
        start, end = bounds
        predicates.extend(["transaction_date >= ?", "transaction_date < ?"])
        parameters.extend([start, end])

    return f"WHERE {' AND '.join(predicates)}", parameters


def _window_bounds(
    window: TimeWindowSpec | None,
    evaluation_date: date | None,
) -> tuple[date, date] | None:
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
