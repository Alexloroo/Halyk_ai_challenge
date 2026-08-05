from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from halyk_covenants.covenants import CovenantRegistry
from halyk_covenants.observability import trace_stage
from halyk_covenants.storage import DuckDBStore

logger = logging.getLogger(__name__)


@dataclass
class ManifestEntry:
    borrower_id: str
    covenant_id: str
    source: str
    document_id: str | None = None
    page: int | None = None
    required: bool = False


@dataclass
class ExpectationManifest:
    entries: list[ManifestEntry] = field(default_factory=list)

    @property
    def expected_pairs(self) -> set[tuple[str, str]]:
        return {(e.borrower_id, e.covenant_id) for e in self.entries}

    @property
    def required_pairs(self) -> set[tuple[str, str]]:
        return {(e.borrower_id, e.covenant_id) for e in self.entries if e.required}


class ManifestBuilder:
    def __init__(self, store: DuckDBStore, registry: CovenantRegistry) -> None:
        self.store = store
        self.registry = registry

    @trace_stage("manifest.build", run_type="chain", tags=("verification", "manifest"))
    def build(
        self,
        questions: Mapping[tuple[str, str], str] | None = None,
    ) -> ExpectationManifest:
        entries: list[ManifestEntry] = []
        seen: set[tuple[str, str, str]] = set()

        if questions:
            for (borrower_id, covenant_id), _question in questions.items():
                key = (borrower_id, covenant_id, "organizer_question")
                if key not in seen:
                    seen.add(key)
                    entries.append(ManifestEntry(
                        borrower_id=borrower_id,
                        covenant_id=covenant_id,
                        source="organizer_question",
                        required=True,
                    ))

        covenants = self.registry.list()
        for spec in covenants:
            group_id = spec.covenant_group_id or spec.covenant_id
            source_doc = spec.source.document_id if spec.source else None
            source_page = spec.source.page if spec.source else None

            for borrower_id in spec.borrower_ids:
                key = (borrower_id, group_id, "detected")
                if key not in seen:
                    seen.add(key)
                    entries.append(ManifestEntry(
                        borrower_id=borrower_id,
                        covenant_id=group_id,
                        source="detected",
                        document_id=source_doc,
                        page=source_page,
                    ))

        self._persist(entries)

        logger.info(
            "Expectation manifest: %d entries (%d required), %d unique pairs",
            len(entries),
            sum(1 for e in entries if e.required),
            len({(e.borrower_id, e.covenant_id) for e in entries}),
        )
        return ExpectationManifest(entries=entries)

    def _persist(self, entries: list[ManifestEntry]) -> None:
        self.store.connection.execute("DELETE FROM expectation_manifest")
        for entry in entries:
            self.store.connection.execute(
                """
                INSERT INTO expectation_manifest
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (borrower_id, covenant_id, source) DO NOTHING
                """,
                [
                    entry.borrower_id,
                    entry.covenant_id,
                    entry.source,
                    entry.document_id,
                    entry.page,
                    entry.required,
                ],
            )

    def load(self) -> ExpectationManifest:
        rows = self.store.connection.execute(
            "SELECT borrower_id, covenant_id, source, document_id, page, required "
            "FROM expectation_manifest"
        ).fetchall()
        return ExpectationManifest(
            entries=[
                ManifestEntry(
                    borrower_id=row[0],
                    covenant_id=row[1],
                    source=row[2],
                    document_id=row[3],
                    page=row[4],
                    required=bool(row[5]),
                )
                for row in rows
            ]
        )
