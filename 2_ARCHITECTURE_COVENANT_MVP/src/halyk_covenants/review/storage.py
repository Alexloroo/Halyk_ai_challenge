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
                reviewer_model VARCHAR,
                prompt_version VARCHAR,
                decision_json JSON NOT NULL,
                PRIMARY KEY (review_run_id, borrower_id, covenant_id)
            )
            """
        )
        self.store.connection.execute(
            "ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS reviewer_model VARCHAR"
        )
        self.store.connection.execute(
            "ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS prompt_version VARCHAR"
        )

    def save(
        self,
        *,
        review_run_id: str,
        evaluation_date: date,
        reviewed: ReviewedResult,
        reviewer_model: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self.store.connection.execute(
            """
            INSERT INTO review_decisions (
                review_run_id, borrower_id, covenant_id, evaluation_date, status,
                reviewer_model, prompt_version, decision_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON))
            ON CONFLICT (review_run_id, borrower_id, covenant_id) DO UPDATE SET
                evaluation_date = excluded.evaluation_date,
                status = excluded.status,
                reviewer_model = excluded.reviewer_model,
                prompt_version = excluded.prompt_version,
                decision_json = excluded.decision_json
            """,
            [
                review_run_id,
                reviewed.result.borrower_id,
                reviewed.result.covenant_id,
                evaluation_date,
                reviewed.review_status,
                reviewer_model,
                prompt_version,
                reviewed.model_dump_json(),
            ],
        )
