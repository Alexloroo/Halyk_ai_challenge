from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from halyk_covenants.borrowers.normalization import normalize_name
from halyk_covenants.domain import Borrower, Transaction
from halyk_covenants.ingestion import read_structured_file

CANONICAL_COLUMNS = (
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
)
REQUIRED_COLUMNS = ("transaction_id", "transaction_date", "amount")
DEFAULT_ALIASES = {"transaction_date": ("transaction_date", "date")}
_DIRECTION_ALIASES = {
    "incoming": "incoming",
    "in": "incoming",
    "credit": "incoming",
    "credit_in": "incoming",
    "входящий": "incoming",
    "входящая": "incoming",
    "outgoing": "outgoing",
    "out": "outgoing",
    "debit": "outgoing",
    "debit_out": "outgoing",
    "исходящий": "outgoing",
    "исходящая": "outgoing",
}


class DuckDBStore:
    """Own the canonical transaction schema and its source-row provenance."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_transactions (
                source_file VARCHAR NOT NULL,
                source_row_number BIGINT NOT NULL,
                source_hash VARCHAR NOT NULL,
                raw_payload JSON NOT NULL
            );

            CREATE TABLE IF NOT EXISTS borrowers (
                borrower_id VARCHAR PRIMARY KEY,
                canonical_name VARCHAR
            );

            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id VARCHAR NOT NULL,
                borrower_id VARCHAR,
                account_id VARCHAR,
                transaction_date DATE NOT NULL,
                amount DECIMAL(38, 6) NOT NULL,
                currency VARCHAR,
                direction VARCHAR,
                counterparty_id VARCHAR,
                counterparty_name VARCHAR,
                purpose VARCHAR,
                source_row_id VARCHAR,
                source_file VARCHAR NOT NULL,
                source_hash VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS borrower_aliases (
                borrower_id VARCHAR NOT NULL,
                alias VARCHAR NOT NULL,
                alias_normalized VARCHAR NOT NULL,
                PRIMARY KEY (borrower_id, alias_normalized)
            );

            CREATE TABLE IF NOT EXISTS borrower_identifiers (
                borrower_id VARCHAR NOT NULL,
                identifier_type VARCHAR NOT NULL,
                identifier_value VARCHAR NOT NULL,
                PRIMARY KEY (borrower_id, identifier_type)
            );

            CREATE TABLE IF NOT EXISTS documents (
                document_id VARCHAR PRIMARY KEY,
                source_path VARCHAR,
                document_sha256 VARCHAR,
                metadata_json JSON
            );

            CREATE TABLE IF NOT EXISTS document_blocks (
                block_id VARCHAR PRIMARY KEY,
                document_id VARCHAR NOT NULL,
                page INTEGER NOT NULL,
                block_type VARCHAR NOT NULL,
                block_json JSON NOT NULL
            );

            CREATE TABLE IF NOT EXISTS covenants (
                covenant_id VARCHAR PRIMARY KEY,
                covenant_group_id VARCHAR,
                effective_from DATE,
                effective_to DATE,
                status VARCHAR NOT NULL,
                source_document_id VARCHAR,
                source_page INTEGER,
                spec_json JSON NOT NULL
            );

            CREATE TABLE IF NOT EXISTS covenant_borrowers (
                covenant_id VARCHAR NOT NULL,
                borrower_id VARCHAR NOT NULL,
                PRIMARY KEY (covenant_id, borrower_id)
            );

            CREATE TABLE IF NOT EXISTS calculations (
                calculation_id VARCHAR PRIMARY KEY,
                covenant_id VARCHAR NOT NULL,
                borrower_id VARCHAR NOT NULL,
                calculation_json JSON NOT NULL
            );

            CREATE TABLE IF NOT EXISTS covenant_results (
                borrower_id VARCHAR NOT NULL,
                covenant_id VARCHAR NOT NULL,
                result_json JSON NOT NULL,
                PRIMARY KEY (borrower_id, covenant_id)
            );

            CREATE TABLE IF NOT EXISTS covenant_result_history (
                run_id VARCHAR NOT NULL,
                evaluation_date DATE NOT NULL,
                borrower_id VARCHAR NOT NULL,
                covenant_id VARCHAR NOT NULL,
                result_json JSON NOT NULL,
                PRIMARY KEY (run_id, borrower_id, covenant_id)
            );

            CREATE TABLE IF NOT EXISTS pipeline_stage_records (
                run_id VARCHAR NOT NULL,
                stage_name VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                record_json JSON NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ingestion_artifacts (
                source_path VARCHAR PRIMARY KEY,
                content_sha256 VARCHAR NOT NULL,
                artifact_type VARCHAR NOT NULL,
                processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS expectation_manifest (
                borrower_id VARCHAR NOT NULL,
                covenant_id VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                document_id VARCHAR,
                page INTEGER,
                required BOOLEAN NOT NULL DEFAULT FALSE,
                PRIMARY KEY (borrower_id, covenant_id, source)
            );
            """
        )

    def load_transactions(
        self,
        path: str | Path,
        column_mapping: Mapping[str, str] | None = None,
    ) -> int:
        """Replace one structured source snapshot atomically.

        Parsing and Pydantic validation happen before the transaction starts. Once mutation begins,
        the previous rows from exactly this resolved source path are deleted and the new snapshot is
        inserted in one DuckDB transaction. Any failure rolls back to the prior complete snapshot.
        """
        source_path = Path(path)
        frame = read_structured_file(source_path)
        mapping = self._resolve_mapping(frame, column_mapping)
        source_file = str(source_path.resolve())

        raw_rows: list[tuple[object, ...]] = []
        canonical_rows: list[tuple[object, ...]] = []
        for zero_based_index, raw_row in frame.iterrows():
            source_row_number = int(zero_based_index) + 2
            payload = {
                str(column): self._json_value(value) for column, value in raw_row.to_dict().items()
            }
            raw_json = json.dumps(
                payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
            source_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
            raw_rows.append((source_file, source_row_number, source_hash, raw_json))

            values = {
                canonical: self._optional_string(raw_row[source]) if source is not None else None
                for canonical, source in mapping.items()
            }
            if values["source_row_id"] is None:
                values["source_row_id"] = f"{source_path.name}:{source_row_number}"
            values["currency"] = self._normalize_currency(values["currency"])
            values["direction"] = self._normalize_direction(values["direction"])
            transaction = Transaction.model_validate(values)
            canonical_rows.append(
                (
                    transaction.transaction_id,
                    transaction.borrower_id,
                    transaction.account_id,
                    transaction.transaction_date,
                    transaction.amount,
                    transaction.currency,
                    transaction.direction,
                    transaction.counterparty_id,
                    transaction.counterparty_name,
                    transaction.purpose,
                    transaction.source_row_id,
                    source_file,
                    source_hash,
                )
            )

        self.connection.execute("BEGIN TRANSACTION")
        try:
            self._load_embedded_borrower_master(source_path)
            self.connection.execute(
                "DELETE FROM raw_transactions WHERE source_file = ?", [source_file]
            )
            self.connection.execute(
                "DELETE FROM transactions WHERE source_file = ?", [source_file]
            )
            if raw_rows:
                self.connection.executemany(
                    "INSERT INTO raw_transactions VALUES (?, ?, ?, ?)",
                    raw_rows,
                )
                self.connection.executemany(
                    """
                    INSERT INTO transactions (
                        transaction_id, borrower_id, account_id, transaction_date, amount,
                        currency, direction, counterparty_id, counterparty_name, purpose,
                        source_row_id, source_file, source_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    canonical_rows,
                )
                self.connection.execute(
                    """
                    INSERT INTO borrowers (borrower_id)
                    SELECT DISTINCT borrower_id
                    FROM transactions
                    WHERE borrower_id IS NOT NULL
                    ON CONFLICT DO NOTHING
                    """
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return len(canonical_rows)

    def _load_embedded_borrower_master(self, path: Path) -> None:
        if path.suffix.casefold() not in {".xlsx", ".xlsm"}:
            return
        workbook = pd.ExcelFile(path)
        sheet = next(
            (name for name in workbook.sheet_names if name.casefold() == "borrowers"), None
        )
        if sheet is None:
            return
        frame = pd.read_excel(path, sheet_name=sheet, dtype=str, keep_default_na=False)
        required = {"borrower_id", "canonical_name"}
        if not required.issubset({str(column) for column in frame.columns}):
            return
        borrowers: list[Borrower] = []
        reserved = {"borrower_id", "canonical_name", "aliases"}
        for _, row in frame.iterrows():
            borrower_id = self._optional_string(row.get("borrower_id"))
            if borrower_id is None:
                continue
            aliases = [
                alias.strip() for alias in str(row.get("aliases", "")).split(";") if alias.strip()
            ]
            identifiers = {
                str(column): value
                for column in frame.columns
                if str(column) not in reserved
                and (value := self._optional_string(row.get(column))) is not None
            }
            borrowers.append(
                Borrower(
                    borrower_id=borrower_id,
                    canonical_name=self._optional_string(row.get("canonical_name")),
                    aliases=aliases,
                    identifiers=identifiers,
                )
            )
        self.save_borrowers(borrowers)

    def save_borrowers(self, borrowers: list[Borrower]) -> None:
        for borrower in borrowers:
            self.connection.execute(
                """
                INSERT INTO borrowers (borrower_id, canonical_name) VALUES (?, ?)
                ON CONFLICT (borrower_id) DO UPDATE SET
                    canonical_name = COALESCE(excluded.canonical_name, borrowers.canonical_name)
                """,
                [borrower.borrower_id, borrower.canonical_name],
            )
            for alias in borrower.aliases:
                self.connection.execute(
                    """
                    INSERT INTO borrower_aliases VALUES (?, ?, ?)
                    ON CONFLICT (borrower_id, alias_normalized) DO UPDATE SET alias = excluded.alias
                    """,
                    [borrower.borrower_id, alias, normalize_name(alias)],
                )
            for identifier_type, identifier_value in borrower.identifiers.items():
                self.connection.execute(
                    """
                    INSERT INTO borrower_identifiers VALUES (?, ?, ?)
                    ON CONFLICT (borrower_id, identifier_type) DO UPDATE SET
                        identifier_value = excluded.identifier_value
                    """,
                    [borrower.borrower_id, identifier_type, identifier_value],
                )

    def list_borrowers(self) -> list[Borrower]:
        rows = self.connection.execute(
            "SELECT borrower_id, canonical_name FROM borrowers ORDER BY borrower_id"
        ).fetchall()
        borrowers: list[Borrower] = []
        for borrower_id, canonical_name in rows:
            aliases = [
                str(row[0])
                for row in self.connection.execute(
                    """
                    SELECT alias FROM borrower_aliases
                    WHERE borrower_id = ? ORDER BY alias_normalized
                    """,
                    [borrower_id],
                ).fetchall()
            ]
            identifiers = {
                str(identifier_type): str(identifier_value)
                for identifier_type, identifier_value in self.connection.execute(
                    """
                    SELECT identifier_type, identifier_value FROM borrower_identifiers
                    WHERE borrower_id = ? ORDER BY identifier_type
                    """,
                    [borrower_id],
                ).fetchall()
            }
            borrowers.append(
                Borrower(
                    borrower_id=str(borrower_id),
                    canonical_name=canonical_name,
                    aliases=aliases,
                    identifiers=identifiers,
                )
            )
        return borrowers

    def _resolve_mapping(
        self,
        frame: pd.DataFrame,
        column_mapping: Mapping[str, str] | None,
    ) -> dict[str, str | None]:
        columns = {str(column) for column in frame.columns}
        requested = dict(column_mapping or {})
        unknown_targets = set(requested) - set(CANONICAL_COLUMNS)
        if unknown_targets:
            unknown = ", ".join(sorted(unknown_targets))
            raise ValueError(f"Unknown canonical columns in mapping: {unknown}")

        resolved: dict[str, str | None] = {}
        for canonical in CANONICAL_COLUMNS:
            source = requested.get(canonical)
            if source is None:
                candidates = DEFAULT_ALIASES.get(canonical, (canonical,))
                source = next((candidate for candidate in candidates if candidate in columns), None)
            if source is not None and source not in columns:
                raise ValueError(f"Mapped source column does not exist: {source}")
            resolved[canonical] = source

        missing = [column for column in REQUIRED_COLUMNS if resolved[column] is None]
        if missing:
            raise ValueError(f"Missing required transaction columns: {', '.join(missing)}")
        return resolved

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if value is None or pd.isna(value):
            return None
        text = str(value).strip()
        return text if text else None

    @staticmethod
    def _normalize_currency(value: str | None) -> str | None:
        return value.upper() if value else None

    @staticmethod
    def _normalize_direction(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_name(value).replace(" ", "_")
        return _DIRECTION_ALIASES.get(normalized, normalized)

    @classmethod
    def _json_value(cls, value: Any) -> object:
        if value is None or pd.isna(value):
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def transaction_exists(self, transaction_id: str) -> bool:
        row = self.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM transactions WHERE transaction_id = ?)",
            [transaction_id],
        ).fetchone()
        return bool(row[0])

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DuckDBStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
