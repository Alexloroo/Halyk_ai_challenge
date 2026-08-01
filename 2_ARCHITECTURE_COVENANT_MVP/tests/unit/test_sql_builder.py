from datetime import date
from decimal import Decimal

import duckdb
import pytest

from halyk_covenants.domain import FilterSpec, TimeWindowSpec
from halyk_covenants.sql import build_where_clause


@pytest.fixture
def transaction_connection() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE transactions (
            transaction_id VARCHAR,
            borrower_id VARCHAR,
            transaction_date DATE,
            amount DECIMAL(38, 6),
            direction VARCHAR,
            purpose VARCHAR
        );
        INSERT INTO transactions VALUES
            ('A', 'B001', DATE '2026-08-01', 10, 'outgoing', 'tax_100%'),
            ('B', 'B001', DATE '2026-08-02', 20, 'incoming', 'invoice'),
            ('C', 'B001', DATE '2026-07-20', 30, 'outgoing', NULL),
            ('D', 'B002', DATE '2026-08-02', 40, 'outgoing', 'tax_100%');
        """
    )
    yield connection
    connection.close()


@pytest.mark.parametrize(
    ("filter_spec", "expected_ids"),
    [
        (FilterSpec(field="direction", operator="eq", value="outgoing"), ["A", "C"]),
        (FilterSpec(field="direction", operator="neq", value="outgoing"), ["B"]),
        (FilterSpec(field="amount", operator="gt", value=10), ["B", "C"]),
        (FilterSpec(field="amount", operator="gte", value=20), ["B", "C"]),
        (FilterSpec(field="amount", operator="lt", value=30), ["A", "B"]),
        (FilterSpec(field="amount", operator="lte", value=20), ["A", "B"]),
        (FilterSpec(field="amount", operator="in", value=[10, 30]), ["A", "C"]),
        (FilterSpec(field="amount", operator="not_in", value=[10, 30]), ["B"]),
        (FilterSpec(field="purpose", operator="contains", value="_100%"), ["A"]),
        (FilterSpec(field="purpose", operator="not_contains", value="tax"), ["B"]),
        (FilterSpec(field="purpose", operator="eq", value=None), ["C"]),
        (FilterSpec(field="purpose", operator="neq", value=None), ["A", "B"]),
    ],
)
def test_filter_operators_select_only_matching_borrower_transactions(
    transaction_connection: duckdb.DuckDBPyConnection,
    filter_spec: FilterSpec,
    expected_ids: list[str],
) -> None:
    where_sql, parameters = build_where_clause("B001", [filter_spec])

    rows = transaction_connection.execute(
        f"SELECT transaction_id FROM transactions {where_sql} ORDER BY transaction_id", parameters
    ).fetchall()

    assert [row[0] for row in rows] == expected_ids


@pytest.mark.parametrize(
    ("window", "evaluation_date", "expected_start", "expected_end"),
    [
        (TimeWindowSpec(type="calendar_day"), date(2026, 8, 2), date(2026, 8, 2), date(2026, 8, 3)),
        (
            TimeWindowSpec(type="calendar_week"),
            date(2026, 8, 2),
            date(2026, 7, 27),
            date(2026, 8, 3),
        ),
        (
            TimeWindowSpec(type="calendar_month"),
            date(2026, 8, 2),
            date(2026, 8, 1),
            date(2026, 9, 1),
        ),
        (
            TimeWindowSpec(type="calendar_quarter"),
            date(2026, 8, 2),
            date(2026, 7, 1),
            date(2026, 10, 1),
        ),
        (
            TimeWindowSpec(type="calendar_year"),
            date(2026, 8, 2),
            date(2026, 1, 1),
            date(2027, 1, 1),
        ),
        (
            TimeWindowSpec(type="rolling_days", rolling_days=7),
            date(2026, 8, 2),
            date(2026, 7, 27),
            date(2026, 8, 3),
        ),
        (
            TimeWindowSpec(type="custom", start_date=date(2026, 4, 1), end_date=date(2026, 4, 30)),
            None,
            date(2026, 4, 1),
            date(2026, 5, 1),
        ),
    ],
)
def test_time_windows_compile_to_half_open_date_ranges(
    window: TimeWindowSpec,
    evaluation_date: date | None,
    expected_start: date,
    expected_end: date,
) -> None:
    where_sql, parameters = build_where_clause(
        "B001", [], time_window=window, evaluation_date=evaluation_date
    )

    assert "transaction_date >= ?" in where_sql
    assert "transaction_date < ?" in where_sql
    assert parameters[-2:] == [expected_start, expected_end]


def test_none_window_adds_no_date_predicate() -> None:
    where_sql, parameters = build_where_clause("B001", [], time_window=TimeWindowSpec(type="none"))

    assert where_sql == "WHERE borrower_id = ?"
    assert parameters == ["B001"]


def test_calendar_window_requires_evaluation_date() -> None:
    with pytest.raises(ValueError, match="evaluation_date is required"):
        build_where_clause("B001", [], time_window=TimeWindowSpec(type="calendar_month"))


def test_field_names_are_allowlisted() -> None:
    malicious = FilterSpec(field="amount); DROP TABLE transactions; --", operator="eq", value=10)

    with pytest.raises(ValueError, match="Unsupported filter field"):
        build_where_clause("B001", [malicious])


def test_filter_values_are_parameters_not_interpolated_into_sql() -> None:
    malicious_value = "outgoing' OR TRUE --"

    where_sql, parameters = build_where_clause(
        "B001", [FilterSpec(field="direction", operator="eq", value=malicious_value)]
    )

    assert malicious_value not in where_sql
    assert parameters == ["B001", malicious_value]


def test_empty_in_filters_have_deterministic_boolean_semantics() -> None:
    in_sql, in_parameters = build_where_clause(
        "B001", [FilterSpec(field="amount", operator="in", value=[])]
    )
    not_in_sql, not_in_parameters = build_where_clause(
        "B001", [FilterSpec(field="amount", operator="not_in", value=[])]
    )

    assert in_sql.endswith("AND FALSE")
    assert not_in_sql.endswith("AND TRUE")
    assert in_parameters == not_in_parameters == ["B001"]


def test_numeric_filter_keeps_decimal_parameter_exact() -> None:
    value = Decimal("10.000001")

    _, parameters = build_where_clause(
        "B001", [FilterSpec(field="amount", operator="eq", value=value)]
    )

    assert parameters[-1] == value
