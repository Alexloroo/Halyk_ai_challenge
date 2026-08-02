from __future__ import annotations

PHYSICAL_TRANSACTION_FIELDS = frozenset(
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
    }
)

DERIVED_TRANSACTION_FIELD_SQL = {
    "weekday": "EXTRACT(ISODOW FROM transaction_date)",
}

FILTER_FIELDS = PHYSICAL_TRANSACTION_FIELDS | frozenset(DERIVED_TRANSACTION_FIELD_SQL)
GROUP_BY_FIELDS = PHYSICAL_TRANSACTION_FIELDS
DATE_FIELDS = frozenset({"transaction_date"})


def transaction_field_sql(field: str) -> str:
    """Return executable SQL for a field from the closed transaction catalog."""
    if field not in FILTER_FIELDS:
        raise ValueError(f"Unsupported transaction field: {field}")
    return DERIVED_TRANSACTION_FIELD_SQL.get(field, field)
