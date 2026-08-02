from __future__ import annotations

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.observability import trace_stage
from halyk_covenants.storage.duckdb_store import DuckDBStore


class CovenantRegistry:
    def __init__(self, store: DuckDBStore) -> None:
        self.store = store

    @trace_stage("covenant.registry.save", run_type="tool", tags=("storage", "covenant"))
    def save(self, spec: CovenantSpec) -> None:
        payload = spec.model_dump_json()
        with self.store.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO covenants (
                    covenant_id, covenant_group_id, effective_from, effective_to, status,
                    source_document_id, source_page, spec_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON))
                ON CONFLICT (covenant_id) DO UPDATE SET
                    covenant_group_id = excluded.covenant_group_id,
                    effective_from = excluded.effective_from,
                    effective_to = excluded.effective_to,
                    status = excluded.status,
                    source_document_id = excluded.source_document_id,
                    source_page = excluded.source_page,
                    spec_json = excluded.spec_json
                """,
                [
                    spec.covenant_id,
                    spec.covenant_group_id,
                    spec.effective_from,
                    spec.effective_to,
                    spec.status,
                    spec.source.document_id,
                    spec.source.page,
                    payload,
                ],
            )
            cursor.execute(
                "DELETE FROM covenant_borrowers WHERE covenant_id = ?",
                [spec.covenant_id],
            )
            cursor.executemany(
                "INSERT INTO covenant_borrowers VALUES (?, ?)",
                [(spec.covenant_id, borrower_id) for borrower_id in spec.borrower_ids],
            )

    @trace_stage("covenant.registry.list", run_type="retriever", tags=("storage", "covenant"))
    def list(self) -> list[CovenantSpec]:
        rows = self.store.connection.execute(
            "SELECT spec_json FROM covenants ORDER BY covenant_id"
        ).fetchall()
        return [CovenantSpec.model_validate_json(row[0]) for row in rows]

    @trace_stage(
        "covenant.registry.for_borrower",
        run_type="retriever",
        tags=("storage", "covenant"),
    )
    def for_borrower(self, borrower_id: str) -> list[CovenantSpec]:
        rows = self.store.connection.execute(
            """
            SELECT c.spec_json
            FROM covenants AS c
            JOIN covenant_borrowers AS cb USING (covenant_id)
            WHERE cb.borrower_id = ?
            ORDER BY c.covenant_id
            """,
            [borrower_id],
        ).fetchall()
        return [CovenantSpec.model_validate_json(row[0]) for row in rows]
