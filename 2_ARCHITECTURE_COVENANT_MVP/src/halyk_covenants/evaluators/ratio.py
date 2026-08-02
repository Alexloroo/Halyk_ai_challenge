from __future__ import annotations

from decimal import Decimal

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.domain.covenant import TRANSACTION_FIELDS
from halyk_covenants.evaluators.base import AggregateEvaluator
from halyk_covenants.storage import DuckDBStore


class RatioEvaluator(AggregateEvaluator):
    metric_type = "ratio"

    def calculate(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> Decimal | None:
        numerator = covenant.metric.numerator
        denominator = covenant.metric.denominator
        if numerator is None or denominator is None:
            raise ValueError("ratio requires numerator and denominator")
        if numerator.metric_type != "sum" or denominator.metric_type != "sum":
            raise ValueError("MVP ratio supports sum numerator and denominator only")
        if numerator.field != "amount" or denominator.field != "amount":
            raise ValueError("MVP ratio supports amount fields only")

        total = db.connection.execute(
            f"SELECT SUM(amount) FROM transactions {where_sql}", parameters
        ).fetchone()[0]
        if total is None or total == 0:
            return None

        if not covenant.group_by:
            numerator_value = total
        else:
            unknown = set(covenant.group_by) - TRANSACTION_FIELDS
            if unknown:
                raise ValueError(f"unsupported ratio group fields: {sorted(unknown)}")
            group_sql = ", ".join(covenant.group_by)
            row = db.connection.execute(
                f"""
                SELECT SUM(amount) AS grouped_amount
                FROM transactions
                {where_sql}
                GROUP BY {group_sql}
                ORDER BY grouped_amount DESC, {group_sql}
                LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                return None
            numerator_value = row[0]
        return Decimal(numerator_value) / Decimal(total)
