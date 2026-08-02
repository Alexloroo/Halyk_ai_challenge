from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from halyk_covenants.covenants import CovenantRegistry
from halyk_covenants.covenants.validation import validate_compiled_spec
from halyk_covenants.domain import (
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    FailureStage,
    FilterSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
)
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.evaluators.temporal import TemporalEvaluationService
from halyk_covenants.pipeline import BatchEvaluationPipeline
from halyk_covenants.storage import DuckDBStore


def _max_spec(
    *,
    covenant_id: str = "COV-START",
    effective_from: date | None = None,
    effective_to: date | None = None,
    status: str = "compiled",
) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        raw_text="Maximum outgoing transaction must not exceed 5,000,000 KZT",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="max", field="amount", unit="KZT"),
        condition=ConditionSpec(
            comparator="<=",
            threshold=Decimal("5000000"),
            unit="KZT",
            currency="KZT",
        ),
        transaction_filters=[FilterSpec(field="currency", operator="eq", value="KZT")],
        time_window=TimeWindowSpec(type="calendar_month"),
        effective_from=effective_from,
        effective_to=effective_to,
        source=SourceRef(document_id="contract.pdf", page=1),
        confidence=1,
        status=status,
    )


def _insert_transaction(store: DuckDBStore, transaction_id: str, when: str, amount: int) -> None:
    store.connection.execute(
        """
        INSERT INTO transactions VALUES
        (?, 'B001', NULL, CAST(? AS DATE), ?, 'KZT', 'outgoing',
         NULL, NULL, NULL, ?, 'fixture.csv', ?)
        """,
        [transaction_id, when, amount, transaction_id, f"hash-{transaction_id}"],
    )


def test_single_version_clips_metric_window_to_effective_from() -> None:
    store = DuckDBStore()
    _insert_transaction(store, "BEFORE", "2026-04-10", 9_000_000)
    _insert_transaction(store, "ACTIVE", "2026-04-20", 4_000_000)
    spec = _max_spec(effective_from=date(2026, 4, 15))

    result = TemporalEvaluationService(EvaluationService(store)).evaluate_versions(
        [spec], "B001", date(2026, 4, 30)
    )

    assert result.number == Decimal("4000000.000000")
    assert result.verdict == "complied"
    store.close()


def test_single_version_clips_metric_window_to_effective_to() -> None:
    store = DuckDBStore()
    _insert_transaction(store, "ACTIVE", "2026-04-10", 4_000_000)
    _insert_transaction(store, "AFTER", "2026-04-20", 9_000_000)
    spec = _max_spec(effective_from=date(2026, 1, 1), effective_to=date(2026, 4, 14))

    result = TemporalEvaluationService(EvaluationService(store)).evaluate_versions(
        [spec], "B001", date(2026, 4, 30)
    )

    assert result.number == Decimal("4000000.000000")
    assert result.verdict == "complied"
    store.close()


def test_reloading_changed_transaction_source_replaces_previous_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "transactions.csv"
    source.write_text(
        "transaction_id,borrower_id,transaction_date,amount,currency\n"
        "OLD,B001,2026-04-01,100,KZT\n",
        encoding="utf-8",
    )
    store = DuckDBStore()
    store.load_transactions(source)

    source.write_text(
        "transaction_id,borrower_id,transaction_date,amount,currency\n"
        "NEW,B001,2026-04-02,200,KZT\n",
        encoding="utf-8",
    )
    store.load_transactions(source)

    rows = store.connection.execute(
        "SELECT transaction_id, amount FROM transactions ORDER BY transaction_id"
    ).fetchall()
    raw_count = store.connection.execute("SELECT COUNT(*) FROM raw_transactions").fetchone()[0]
    assert rows == [("NEW", Decimal("200.000000"))]
    assert raw_count == 1
    store.close()


def test_derived_weekday_field_is_valid_compiler_output() -> None:
    spec = _max_spec().model_copy(
        update={
            "transaction_filters": [FilterSpec(field="weekday", operator="in", value=[6, 7])]
        }
    )

    errors = validate_compiled_spec(
        spec,
        clause=spec.raw_text,
        allowed_borrower_ids=["B001"],
    )

    assert not any("field unsupported" in error for error in errors)


def test_unsupported_spec_is_not_executed_as_a_compiled_rule() -> None:
    store = DuckDBStore()
    _insert_transaction(store, "TX-1", "2026-04-10", 9_000_000)
    spec = _max_spec(status="unsupported")

    result = EvaluationService(store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.verdict == "unknown"
    assert result.status == "failed"
    assert result.failure_stage == FailureStage.COMPILATION
    store.close()


class _WrongVerdictService:
    def evaluate(
        self,
        covenant: CovenantSpec,
        borrower_id: str,
        evaluation_date: date | None = None,
    ) -> CovenantResult:
        del evaluation_date
        return CovenantResult(
            borrower_id=borrower_id,
            covenant_id=covenant.covenant_id,
            verdict="complied",
            number=Decimal("9000000"),
            number_unit="KZT",
            status="success",
        )


def test_batch_pipeline_runs_pair_verification_before_reporting_valid() -> None:
    store = DuckDBStore()
    spec = _max_spec()
    CovenantRegistry(store).save(spec)

    report = BatchEvaluationPipeline(store, service=_WrongVerdictService()).run(date(2026, 4, 30))  # type: ignore[arg-type]

    assert report.verification.valid is False
    assert any(issue.code == "verdict_mismatch" for issue in report.verification.issues)
    store.close()
