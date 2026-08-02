from halyk_covenants.domain import FilterSpec
from halyk_covenants.sql import compile_filter


def test_weekday_filter_compiles_to_safe_duckdb_expression() -> None:
    sql, parameters = compile_filter(
        FilterSpec(field="weekday", operator="in", value=[6, 7])
    )

    assert sql == "EXTRACT(ISODOW FROM transaction_date) IN (?, ?)"
    assert parameters == [6, 7]


def test_weekday_filter_does_not_accept_arbitrary_sql_field() -> None:
    try:
        compile_filter(
            FilterSpec(
                field="amount); DROP TABLE transactions; --",
                operator="eq",
                value=1,
            )
        )
    except ValueError as exc:
        assert "Unsupported filter field" in str(exc)
    else:
        raise AssertionError("unsafe field unexpectedly compiled")
