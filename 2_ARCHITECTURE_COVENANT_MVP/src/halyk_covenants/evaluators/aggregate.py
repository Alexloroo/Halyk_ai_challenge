from decimal import Decimal

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.evaluators.base import AggregateEvaluator, Numeric
from halyk_covenants.sql import ALLOWED_FILTER_FIELDS
from halyk_covenants.storage import DuckDBStore

NUMERIC_FIELDS = frozenset({"amount"})


def _metric_field(covenant: CovenantSpec, allowed_fields: frozenset[str]) -> str:
    field = covenant.metric.field
    if field is None:
        raise ValueError(f"{covenant.metric.metric_type} metric requires a field")
    if field not in allowed_fields:
        raise ValueError(f"unsupported metric field: {field}")
    return field


class SumEvaluator(AggregateEvaluator):
    metric_type = "sum"

    def calculate(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> Decimal:
        field = _metric_field(covenant, NUMERIC_FIELDS)
        row = db.connection.execute(
            f"SELECT SUM({field}) FROM transactions {where_sql}", parameters
        ).fetchone()
        return row[0] if row[0] is not None else Decimal("0.000000")


class CountEvaluator(AggregateEvaluator):
    metric_type = "count"

    def calculate(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> int:
        if covenant.metric.field is None:
            expression = "*"
        else:
            expression = _metric_field(covenant, ALLOWED_FILTER_FIELDS)
        row = db.connection.execute(
            f"SELECT COUNT({expression}) FROM transactions {where_sql}", parameters
        ).fetchone()
        return int(row[0])


class MaxEvaluator(AggregateEvaluator):
    metric_type = "max"

    def calculate(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> Decimal | None:
        field = _metric_field(covenant, NUMERIC_FIELDS)
        return db.connection.execute(
            f"SELECT MAX({field}) FROM transactions {where_sql}", parameters
        ).fetchone()[0]

    def select_evidence(
        self,
        covenant: CovenantSpec,
        borrower_id: str,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> str | None:
        del borrower_id
        field = _metric_field(covenant, NUMERIC_FIELDS)
        row = db.connection.execute(
            f"""
            SELECT transaction_id
            FROM transactions
            {where_sql} AND {field} IS NOT NULL
            ORDER BY {field} DESC, transaction_date ASC, transaction_id ASC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        return None if row is None else str(row[0])


class MinEvaluator(AggregateEvaluator):
    metric_type = "min"

    def calculate(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> Decimal | None:
        field = _metric_field(covenant, NUMERIC_FIELDS)
        return db.connection.execute(
            f"SELECT MIN({field}) FROM transactions {where_sql}", parameters
        ).fetchone()[0]


class AverageEvaluator(AggregateEvaluator):
    metric_type = "avg"

    def calculate(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> Numeric | None:
        field = _metric_field(covenant, NUMERIC_FIELDS)
        total, count = db.connection.execute(
            f"SELECT SUM({field}), COUNT({field}) FROM transactions {where_sql}", parameters
        ).fetchone()
        if count == 0:
            return None
        return total / Decimal(count)
