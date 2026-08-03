from __future__ import annotations

from datetime import date

from halyk_covenants.review.models import ReviewedResult
from halyk_covenants.storage import DuckDBStore


class ReviewDecisionStore:
    def __init__(self, store: DuckDBStore) -> None:
        self.store = store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.store.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_decisions (
                review_run_id VARCHAR NOT NULL,
                borrower_id VARCHAR NOT NULL,
                covenant_id VARCHAR NOT NULL,
                evaluation_date DATE NOT NULL,
                status VARCHAR NOT NULL,
                decision_json JSON NOT NULL,
                PRIMARY KEY (review_run_id, borrower_id, covenant_id)
            )
            """
        )

    def save(
        self,
        *,
        review_run_id: str,
        evaluation_date: date,
        reviewed: ReviewedResult,
    ) -> None:
        self.store.connection.execute(
            """
            INSERT INTO review_decisions VALUES (?, ?, ?, ?, ?, CAST(? AS JSON))
            ON CONFLICT (review_run_id, borrower_id, covenant_id) DO UPDATE SET
                evaluation_date = excluded.evaluation_date,
                status = excluded.status,
                decision_json = excluded.decision_json
            """,
            [
                review_run_id,
                reviewed.result.borrower_id,
                reviewed.result.covenant_id,
                evaluation_date,
                reviewed.review_status,
                reviewed.model_dump_json(),
            ],
        )
