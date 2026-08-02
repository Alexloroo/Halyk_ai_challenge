from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.borrowers import BorrowerClaim, BorrowerResolver
from halyk_covenants.covenants import CovenantDetector, CovenantRegistry
from halyk_covenants.covenants.detector import CovenantCandidate
from halyk_covenants.documents.retrieval import HybridRetriever
from halyk_covenants.domain import DocumentBlock
from halyk_covenants.ingestion import PDFIngestor
from halyk_covenants.observability import trace_stage
from halyk_covenants.storage import DuckDBStore

STRUCTURED_SUFFIXES = frozenset({".csv", ".xlsx", ".xlsm", ".parquet"})
TRANSACTION_SEMANTIC_CATALOG = """SEMANTIC_CATALOG:
transactions.transaction_id: string transaction identifier
transactions.borrower_id: string borrower identifier
transactions.transaction_date: DATE used for covenant periods
transactions.amount: DECIMAL(38,6) exact transaction amount
transactions.currency: normalized uppercase currency code such as KZT or USD
transactions.direction: normalized values incoming or outgoing
transactions.counterparty_id: optional string
transactions.counterparty_name: optional string
transactions.purpose: optional payment purpose
transactions.source_row_id: source provenance string
derived.weekday: ISO weekday 1=Monday ... 7=Sunday
"""


class PreprocessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    scanned_files: int = Field(ge=0)
    skipped_unchanged_files: int = Field(default=0, ge=0)
    loaded_transaction_rows: int = Field(default=0, ge=0)
    parsed_documents: int = Field(default=0, ge=0)
    detected_candidates: int = Field(default=0, ge=0)
    compiled_covenants: int = Field(default=0, ge=0)
    failed_compilations: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)


class PreprocessPipeline:
    def __init__(
        self,
        store: DuckDBStore,
        *,
        pdf_ingestor: PDFIngestor | None = None,
        detector: CovenantDetector | None = None,
        compiler_graph: Any | None = None,
        registry: CovenantRegistry | None = None,
        progress: Callable[[str], None] | None = None,
        compiler_context_k: int = 12,
    ) -> None:
        if compiler_context_k <= 0:
            raise ValueError("compiler_context_k must be positive")
        self.store = store
        self.pdf_ingestor = pdf_ingestor or PDFIngestor()
        self.detector = detector or CovenantDetector()
        self.compiler_graph = compiler_graph
        self.registry = registry or CovenantRegistry(store)
        self.progress = progress or (lambda message: None)
        self.compiler_context_k = compiler_context_k

    @trace_stage("pipeline.preprocess", run_type="chain", tags=("pipeline", "preprocessing"))
    def run(self, input_root: Path) -> PreprocessReport:
        started = datetime.now(UTC)
        run_id = str(uuid4())
        files = sorted(
            (path for path in input_root.rglob("*") if path.is_file()),
            key=lambda path: (
                0
                if path.suffix.casefold() in STRUCTURED_SUFFIXES
                else 1
                if path.suffix.casefold() == ".pdf"
                else 2,
                str(path),
            ),
        )
        report = PreprocessReport(run_id=run_id, scanned_files=len(files))
        for file_index, path in enumerate(files, start=1):
            prefix = f"[preprocess {file_index}/{len(files)}] {path.name}"
            self.progress(f"{prefix}: started")
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if self._is_unchanged(path, digest):
                    report.skipped_unchanged_files += 1
                    self.progress(f"{prefix}: skipped (unchanged)")
                    continue
                suffix = path.suffix.casefold()
                if suffix in STRUCTURED_SUFFIXES:
                    report.loaded_transaction_rows += self._load_structured(path)
                elif suffix == ".pdf":
                    document_counts = self._load_pdf(path, digest)
                    report.parsed_documents += 1
                    report.detected_candidates += document_counts[0]
                    report.compiled_covenants += document_counts[1]
                    report.failed_compilations += document_counts[2]
                else:
                    self.progress(f"{prefix}: ignored (unsupported file type)")
                    continue
                self._mark_processed(path, digest, suffix.lstrip("."))
                self.progress(f"{prefix}: completed")
            except Exception as exc:
                report.errors.append(f"{path}: {exc}")
                self.progress(f"{prefix}: failed: {type(exc).__name__}: {exc}")
        self._record_run(run_id, started, report)
        return report

    @trace_stage("pipeline.preprocess.structured", run_type="tool", tags=("preprocessing",))
    def _load_structured(self, path: Path) -> int:
        return self.store.load_transactions(path)

    @trace_stage("pipeline.preprocess.document", run_type="chain", tags=("preprocessing",))
    def _load_pdf(self, path: Path, digest: str) -> tuple[int, int, int]:
        blocks = self._annotate_borrower_scopes(self.pdf_ingestor.ingest(path))
        document_id = blocks[0].document_id if blocks else digest
        self.store.connection.execute(
            """
            INSERT INTO documents VALUES (?, ?, ?, CAST(? AS JSON))
            ON CONFLICT (document_id) DO UPDATE SET
                source_path = excluded.source_path,
                document_sha256 = excluded.document_sha256,
                metadata_json = excluded.metadata_json
            """,
            [document_id, str(path.resolve()), digest, json.dumps({"block_count": len(blocks)})],
        )
        for block in blocks:
            self.store.connection.execute(
                """
                INSERT INTO document_blocks VALUES (?, ?, ?, ?, CAST(? AS JSON))
                ON CONFLICT (block_id) DO UPDATE SET block_json = excluded.block_json
                """,
                [
                    block.block_id,
                    block.document_id,
                    block.page,
                    block.block_type,
                    block.model_dump_json(),
                ],
            )

        candidates = self.detector.detect(blocks)
        if self.compiler_graph is None:
            return len(candidates), 0, 0

        borrower_rows = self.store.connection.execute(
            "SELECT borrower_id FROM borrowers ORDER BY borrower_id"
        ).fetchall()
        only_borrower = str(borrower_rows[0][0]) if len(borrower_rows) == 1 else None
        compiled = failed = 0

        retriever = HybridRetriever()
        stored_blocks = self._stored_document_blocks()
        retriever.index(stored_blocks)
        for candidate_index, candidate in enumerate(candidates, start=1):
            if not candidate.borrower_ids and only_borrower:
                candidate = candidate.model_copy(update={"borrower_ids": [only_borrower]})
            document_context = self._compiler_context(
                blocks=stored_blocks,
                candidate=candidate,
                retriever=retriever,
            )
            self.progress(
                f"[compile {candidate_index}/{len(candidates)}] {path.name} "
                f"{candidate.candidate_id}: waiting for DeepSeek"
            )
            final = self.compiler_graph.invoke(
                {"candidate": candidate, "context": document_context, "attempt": 0}
            )
            if final.get("status") == "compiled":
                for spec in final["outcome"].specs:
                    self.registry.save(spec)
                    compiled += 1
                self.progress(
                    f"[compile {candidate_index}/{len(candidates)}] {path.name} "
                    f"{candidate.candidate_id}: compiled"
                )
            else:
                failed += 1
                self.progress(
                    f"[compile {candidate_index}/{len(candidates)}] {path.name} "
                    f"{candidate.candidate_id}: failed"
                )
        return len(candidates), compiled, failed

    def _stored_document_blocks(self) -> list[DocumentBlock]:
        rows = self.store.connection.execute(
            "SELECT block_json FROM document_blocks ORDER BY document_id, page, block_id"
        ).fetchall()
        return [DocumentBlock.model_validate_json(row[0]) for row in rows]

    def _compiler_context(
        self,
        *,
        blocks: list[DocumentBlock],
        candidate: CovenantCandidate,
        retriever: HybridRetriever,
    ) -> str:
        relevant = retriever.search(candidate.raw_text, k=self.compiler_context_k)
        selected: dict[str, DocumentBlock] = {}
        allowed_borrowers = set(candidate.borrower_ids)
        for item in relevant:
            block = item.block
            if allowed_borrowers and block.borrower_ids and not allowed_borrowers.intersection(block.borrower_ids):
                continue
            selected[block.block_id] = block

        source_document = candidate.source.document_id
        source_page = candidate.source.page
        if source_document is not None and source_page is not None:
            for block in blocks:
                if block.document_id == source_document and abs(block.page - source_page) <= 1:
                    selected[block.block_id] = block

        ordered = sorted(
            selected.values(),
            key=lambda block: (
                block.document_id != source_document,
                abs(block.page - source_page) if source_page is not None else block.page,
                block.document_id,
                block.page,
                block.block_id,
            ),
        )[: self.compiler_context_k + 8]
        context_lines = [
            f"[document={block.document_id} page={block.page} type={block.block_type}] {block.text}"
            for block in ordered
            if block.text.strip()
        ]
        context = "\n".join(context_lines) or candidate.raw_text
        return f"{TRANSACTION_SEMANTIC_CATALOG}\nRETRIEVED_DOCUMENT_CONTEXT:\n{context}"

    @trace_stage(
        "pipeline.preprocess.borrower_scope",
        run_type="tool",
        tags=("preprocessing", "borrower"),
    )
    def _annotate_borrower_scopes(self, blocks: list[DocumentBlock]) -> list[DocumentBlock]:
        borrowers = self.store.list_borrowers()
        if not borrowers:
            return blocks
        resolver = BorrowerResolver(borrowers)
        current_scope: list[str] = []
        current_page: int | None = None
        scoped: list[DocumentBlock] = []
        for block in blocks:
            if current_page is None or block.page != current_page:
                current_scope = []
                current_page = block.page
            resolved_ids = list(block.borrower_ids)
            if not resolved_ids:
                resolved_ids = self._exact_borrower_mentions(block.text, borrowers)
            if not resolved_ids:
                claim = self._borrower_name_claim(block.text)
                if claim:
                    resolution = resolver.resolve(BorrowerClaim(name=claim))
                    if resolution.status.startswith("resolved_"):
                        resolved_ids = resolution.borrower_ids
            if resolved_ids:
                current_scope = sorted(set(resolved_ids))
            scoped.append(block.model_copy(update={"borrower_ids": list(current_scope)}))
        return scoped

    @staticmethod
    def _exact_borrower_mentions(text: str, borrowers: list[Any]) -> list[str]:
        matched: list[str] = []
        for borrower in borrowers:
            values = [borrower.borrower_id, *borrower.identifiers.values()]
            if any(
                re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", text, re.IGNORECASE)
                for value in values
                if value
            ):
                matched.append(borrower.borrower_id)
        return sorted(set(matched))

    @staticmethod
    def _borrower_name_claim(text: str) -> str | None:
        match = re.search(
            r"(?:за[её]мщик|borrower)\s*(?::|[-–—])\s*([^,\n(]+)",
            text,
            flags=re.IGNORECASE,
        )
        return match.group(1).strip() if match else None

    def _is_unchanged(self, path: Path, digest: str) -> bool:
        row = self.store.connection.execute(
            "SELECT content_sha256 FROM ingestion_artifacts WHERE source_path = ?",
            [str(path.resolve())],
        ).fetchone()
        return row is not None and row[0] == digest

    def _mark_processed(self, path: Path, digest: str, artifact_type: str) -> None:
        self.store.connection.execute(
            """
            INSERT INTO ingestion_artifacts (source_path, content_sha256, artifact_type)
            VALUES (?, ?, ?)
            ON CONFLICT (source_path) DO UPDATE SET
                content_sha256 = excluded.content_sha256,
                artifact_type = excluded.artifact_type,
                processed_at = now()
            """,
            [str(path.resolve()), digest, artifact_type],
        )

    def _record_run(
        self,
        run_id: str,
        started: datetime,
        report: PreprocessReport,
    ) -> None:
        finished = datetime.now(UTC)
        status = "partial" if report.errors else "success"
        record = {
            "run_id": run_id,
            "stage_name": "pipeline.preprocess",
            "status": status,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "error_summary": "; ".join(report.errors) or None,
        }
        self.store.connection.execute(
            "INSERT INTO pipeline_stage_records VALUES (?, ?, ?, CAST(? AS JSON))",
            [run_id, "pipeline.preprocess", started, json.dumps(record)],
        )
