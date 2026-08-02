from collections.abc import Iterable
from typing import Any

from halyk_covenants.domain import FilterSpec

ALLOWED_FILTER_FIELDS = frozenset(
    {
        "transaction_id",
        "borrower_id",
        "account_id",
        "transaction_date",
        "amount",
        "currency",
        "direction",
        "counterparty_id",
        "counterparty_name",
        "purpose",
        "source_row_id",
        "weekday",
    }
)

_DERIVED_FIELD_SQL = {
    "weekday": "EXTRACT(ISODOW FROM transaction_date)",
}

_BINARY_OPERATORS = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def compile_filter(filter_spec: FilterSpec) -> tuple[str, list[Any]]:
    """Compile one validated filter to parameterized DuckDB SQL.

    Derived fields are selected from a closed mapping. User/model supplied field names are never
    interpolated as arbitrary SQL expressions.
    """
    field = filter_spec.field
    if field not in ALLOWED_FILTER_FIELDS:
        raise ValueError(f"Unsupported filter field: {field}")
    field_sql = _DERIVED_FIELD_SQL.get(field, field)

    operator = filter_spec.operator
    value = filter_spec.value
    if operator in {"eq", "neq"} and value is None:
        null_operator = "IS NULL" if operator == "eq" else "IS NOT NULL"
        return f"{field_sql} {null_operator}", []

    if operator in _BINARY_OPERATORS:
        return f"{field_sql} {_BINARY_OPERATORS[operator]} ?", [value]

    if operator in {"in", "not_in"}:
        values = _collection_values(value, operator)
        if not values:
            return ("FALSE" if operator == "in" else "TRUE"), []
        sql_operator = "IN" if operator == "in" else "NOT IN"
        placeholders = ", ".join("?" for _ in values)
        return f"{field_sql} {sql_operator} ({placeholders})", values

    if operator in {"contains", "not_contains"}:
        if not isinstance(value, str):
            raise ValueError(f"{operator} requires a string value")
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql_operator = "LIKE" if operator == "contains" else "NOT LIKE"
        return f"CAST({field_sql} AS VARCHAR) {sql_operator} ? ESCAPE '\\'", [f"%{escaped}%"]

    raise ValueError(f"Unsupported filter operator: {operator}")


def _collection_values(value: Any, operator: str) -> list[Any]:
    if isinstance(value, (str, bytes, dict)) or not isinstance(value, Iterable):
        raise ValueError(f"{operator} requires a non-string iterable value")
    return list(value)
