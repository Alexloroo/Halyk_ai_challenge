from __future__ import annotations

import hashlib

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.observability import trace_stage
from halyk_covenants.storage.duckdb_store import DuckDBStore


class CovenantRegistry:
    def __init__(self, store: DuckDBStore) -> None:
        self.store = store

    @trace_stage("covenant.registry.save", run_type="tool", tags=("storage", "covenant"))
    def save(self, spec: CovenantSpec) -> None:
        spec = self._resolve_version_collision(spec)
        self._write(spec)

    def _resolve_version_collision(self, spec: CovenantSpec) -> CovenantSpec:
        row = self.store.connection.execute(
            "SELECT spec_json FROM covenants WHERE covenant_id = ?",
            [spec.covenant_id],
        ).fetchone()
        if row is None:
            return spec
        existing = CovenantSpec.model_validate_json(row[0])
        if self._same_version(existing, spec):
            return spec

        family_id = spec.covenant_group_id or spec.covenant_id
        if existing.covenant_group_id != family_id:
            self._write(existing.model_copy(update={"covenant_group_id": family_id}))

        semantic_identity = "|".join(
            [
                spec.source.document_id or "",
                str(spec.source.page or ""),
                spec.effective_from.isoformat() if spec.effective_from else "",
                spec.effective_to.isoformat() if spec.effective_to else "",
                spec.raw_text,
                spec.metric.model_dump_json(),
                spec.condition.model_dump_json(),
                repr([item.model_dump(mode="json") for item in spec.transaction_filters]),
                repr([item.model_dump(mode="json") for item in spec.exclusions]),
            ]
        )
        suffix = hashlib.sha256(semantic_identity.encode("utf-8")).hexdigest()[:10].upper()
        return spec.model_copy(
            update={
                "covenant_id": f"{family_id}@{suffix}",
                "covenant_group_id": family_id,
            }
        )

    @staticmethod
    def _same_version(left: CovenantSpec, right: CovenantSpec) -> bool:
        return (
            left.source.document_id == right.source.document_id
            and left.source.page == right.source.page
            and left.effective_from == right.effective_from
            and left.effective_to == right.effective_to
            and left.raw_text == right.raw_text
            and left.metric == right.metric
            and left.condition == right.condition
            and left.transaction_filters == right.transaction_filters
            and left.exclusions == right.exclusions
            and left.group_by == right.group_by
        )

    def _write(self, spec: CovenantSpec) -> None:
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
