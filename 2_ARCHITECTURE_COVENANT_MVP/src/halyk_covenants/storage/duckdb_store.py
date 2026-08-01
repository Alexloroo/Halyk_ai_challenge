from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from halyk_covenants.domain import Transaction
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
            """
        )

    def load_transactions(
        self,
        path: str | Path,
        column_mapping: Mapping[str, str] | None = None,
    ) -> int:
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
        return len(canonical_rows)

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
        text = str(value)
        return text if text != "" else None

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
