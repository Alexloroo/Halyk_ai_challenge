from __future__ import annotations

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.evaluators.base import AggregateEvaluator
from halyk_covenants.storage import DuckDBStore


class FrequencyEvaluator(AggregateEvaluator):
    metric_type = "frequency"

    def calculate(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> int:
        # Frequency is the worst daily bucket within the selected evaluation window.
        row = db.connection.execute(
            f"""
            SELECT COALESCE(MAX(bucket_count), 0)
            FROM (
                SELECT CAST({covenant.date_field} AS DATE) AS bucket, COUNT(*) AS bucket_count
                FROM transactions
                {where_sql}
                GROUP BY bucket
            )
            """,
            parameters,
        ).fetchone()
        return int(row[0])
