from decimal import Decimal
from pathlib import Path

from halyk_covenants.domain import Borrower, DocumentBlock, SourceRef
from halyk_covenants.pipeline import PreprocessPipeline
from halyk_covenants.storage import DuckDBStore


def test_structured_preprocessing_is_hash_idempotent(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "transactions.csv").write_text(
        "transaction_id,borrower_id,date,amount,currency\nT1,B001,2026-04-01,10,KZT\n",
        encoding="utf-8",
    )
    with DuckDBStore(tmp_path / "pipeline.duckdb") as store:
        pipeline = PreprocessPipeline(store)

        first = pipeline.run(inputs)
        second = pipeline.run(inputs)

        assert first.loaded_transaction_rows == 1
        assert second.loaded_transaction_rows == 0
        assert second.skipped_unchanged_files == 1
        assert store.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_document_blocks_inherit_resolved_borrower_scope(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "scope.duckdb") as store:
        store.save_borrowers(
            [
                Borrower(
                    borrower_id="B001",
                    canonical_name="ТОО Альфа Трейд",
                    aliases=["ALFA TRADE LLP"],
                )
            ]
        )
        pipeline = PreprocessPipeline(store)
        blocks = [
            DocumentBlock(
                block_id="b1",
                document_id="d1",
                page=1,
                block_type="text",
                text="Заёмщик: ALFA TRADE LLP, внутренний идентификатор B001.",
                extraction_method="native",
                confidence=Decimal("1"),
                source=SourceRef(document_id="d1", page=1),
            ),
            DocumentBlock(
                block_id="b2",
                document_id="d1",
                page=1,
                block_type="text",
                text="Месячный объём не должен превышать 10 000 000 KZT.",
                extraction_method="native",
                confidence=Decimal("1"),
                source=SourceRef(document_id="d1", page=1),
            ),
        ]

        scoped = pipeline._annotate_borrower_scopes(blocks)

        assert scoped[0].borrower_ids == ["B001"]
        assert scoped[1].borrower_ids == ["B001"]


def test_preprocess_reports_file_progress(tmp_path: Path) -> None:
    source = tmp_path / "transactions.csv"
    source.write_text(
        "transaction_id,borrower_id,date,amount\nTX1,B001,2026-04-01,10\n",
        encoding="utf-8",
    )
    messages: list[str] = []

    with DuckDBStore(":memory:") as store:
        report = PreprocessPipeline(store, progress=messages.append).run(tmp_path)

    assert report.loaded_transaction_rows == 1
    assert any("transactions.csv" in message for message in messages)
    assert any("completed" in message for message in messages)
