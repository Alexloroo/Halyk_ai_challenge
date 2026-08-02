from collections.abc import Iterable
from typing import Any

from halyk_covenants.domain import FilterSpec
from halyk_covenants.domain.transaction_fields import FILTER_FIELDS, transaction_field_sql

ALLOWED_FILTER_FIELDS = FILTER_FIELDS

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

    Field names come from one closed catalog shared with compiler validation. Values are always
    bound parameters; model/user text is never interpolated as an arbitrary SQL expression.
    """
    field = filter_spec.field
    if field not in ALLOWED_FILTER_FIELDS:
        raise ValueError(f"Unsupported filter field: {field}")
    field_sql = transaction_field_sql(field)

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
