import json
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from halyk_covenants.storage import DuckDBStore

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_csv_ingestion_preserves_ids_decimal_and_raw_provenance(tmp_path: Path) -> None:
    store = DuckDBStore(tmp_path / "transactions.duckdb")

    loaded = store.load_transactions(FIXTURES / "transactions.csv")

    assert loaded == 2
    rows = store.connection.execute(
        """
        SELECT transaction_id, borrower_id, amount, transaction_date, source_row_id
        FROM transactions
        ORDER BY transaction_id
        """
    ).fetchall()
    assert rows == [
        ("0001", "000341", Decimal("5000000.123456"), pd.Timestamp("2026-04-01").date(), "SRC-001"),
        ("0002", "000341", Decimal("6000000.000001"), pd.Timestamp("2026-04-10").date(), "SRC-002"),
    ]

    amount_type = store.connection.execute(
        """
        SELECT data_type
        FROM duckdb_columns()
        WHERE table_name = 'transactions' AND column_name = 'amount'
        """
    ).fetchone()[0]
    assert amount_type == "DECIMAL(38,6)"

    raw_payload = store.connection.execute(
        "SELECT raw_payload FROM raw_transactions ORDER BY source_row_number LIMIT 1"
    ).fetchone()[0]
    assert json.loads(raw_payload)["memo_code"] == "M-01"
    store.close()


def test_ingestion_populates_borrower_registry_with_string_ids(tmp_path: Path) -> None:
    store = DuckDBStore(tmp_path / "borrowers.duckdb")

    store.load_transactions(FIXTURES / "transactions.csv")

    borrowers = store.connection.execute(
        "SELECT borrower_id FROM borrowers ORDER BY borrower_id"
    ).fetchall()
    assert borrowers == [("000341",)]
    store.close()


@pytest.mark.parametrize("extension", [".xlsx", ".parquet"])
def test_excel_and_parquet_ingestion_use_the_same_canonical_contract(
    tmp_path: Path, extension: str
) -> None:
    source = tmp_path / f"transactions{extension}"
    frame = pd.DataFrame(
        [
            {
                "transaction_id": "0007",
                "borrower_id": "000099",
                "date": "2026-05-01",
                "amount": "42.000001",
                "currency": "KZT",
                "direction": "incoming",
            }
        ]
    )
    if extension == ".xlsx":
        frame.to_excel(source, index=False)
    else:
        frame.to_parquet(source, index=False)

    store = DuckDBStore(tmp_path / f"{extension[1:]}.duckdb")

    assert store.load_transactions(source) == 1
    assert store.connection.execute(
        "SELECT transaction_id, borrower_id, amount FROM transactions"
    ).fetchone() == ("0007", "000099", Decimal("42.000001"))
    store.close()


def test_duplicate_source_rows_are_detected_but_not_silently_removed(tmp_path: Path) -> None:
    source = tmp_path / "duplicates.csv"
    source.write_text(
        "transaction_id,borrower_id,date,amount\n"
        "TX-1,B001,2026-04-01,10.00\n"
        "TX-1,B001,2026-04-01,10.00\n",
        encoding="utf-8",
    )
    store = DuckDBStore(tmp_path / "duplicates.duckdb")

    assert store.load_transactions(source) == 2

    transaction_count = store.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    hashes = store.connection.execute(
        "SELECT source_hash FROM transactions ORDER BY source_row_id"
    ).fetchall()
    assert transaction_count == 2
    assert hashes[0][0] == hashes[1][0]
    store.close()


def test_column_mapping_adapts_noncanonical_source_headers(tmp_path: Path) -> None:
    source = tmp_path / "mapped.csv"
    source.write_text(
        "tx,client,operation_date,value\nT-1,0012,2026-06-01,99.990001\n",
        encoding="utf-8",
    )
    store = DuckDBStore(tmp_path / "mapped.duckdb")

    loaded = store.load_transactions(
        source,
        column_mapping={
            "transaction_id": "tx",
            "borrower_id": "client",
            "transaction_date": "operation_date",
            "amount": "value",
        },
    )

    assert loaded == 1
    assert store.connection.execute(
        "SELECT transaction_id, borrower_id, amount FROM transactions"
    ).fetchone() == ("T-1", "0012", Decimal("99.990001"))
    store.close()


def test_unsupported_structured_file_extension_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "transactions.json"
    source.write_text("[]", encoding="utf-8")
    store = DuckDBStore(tmp_path / "unsupported.duckdb")

    with pytest.raises(ValueError, match="Unsupported structured file format"):
        store.load_transactions(source)
    store.close()
