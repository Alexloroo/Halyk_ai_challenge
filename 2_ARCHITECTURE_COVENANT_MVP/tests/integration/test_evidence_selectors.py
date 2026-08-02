from datetime import date
from pathlib import Path

import pytest

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantSpec,
    EvidenceMode,
    FilterSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
)
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.storage import DuckDBStore


@pytest.fixture
def service(tmp_path: Path) -> EvaluationService:
    source = tmp_path / "evidence.csv"
    source.write_text(
        "transaction_id,borrower_id,date,amount,currency,direction,counterparty_id\n"
        "A001,B001,2026-04-01,10,KZT,outgoing,OK\n"
        "A002,B001,2026-04-01,20,KZT,outgoing,OK\n"
        "A003,B001,2026-04-01,30,KZT,outgoing,OK\n"
        "A004,B001,2026-04-01,40,KZT,outgoing,BANNED\n"
        "A005,B001,2026-04-02,40,KZT,outgoing,OK\n",
        encoding="utf-8",
    )
    store = DuckDBStore(tmp_path / "evidence.duckdb")
    store.load_transactions(source)
    yield EvaluationService(store)
    store.close()


def spec(
    covenant_id: str,
    metric_type: str,
    threshold: int,
    evidence_mode: EvidenceMode,
    *,
    filters: list[FilterSpec] | None = None,
) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        raw_text="Evidence rule",
        borrower_ids=["B001"],
        metric=MetricSpec(
            metric_type=metric_type,  # type: ignore[arg-type]
            field="amount" if metric_type == "max" else "transaction_id",
        ),
        condition=ConditionSpec(comparator="<=", threshold=threshold),
        transaction_filters=filters or [],
        time_window=TimeWindowSpec(type="calendar_month"),
        evidence_mode=evidence_mode,
        source=SourceRef(document_id="fixture", page=1),
        confidence=1,
    )


def test_count_trigger_is_limit_plus_one_in_chronological_order(
    service: EvaluationService,
) -> None:
    result = service.evaluate(
        spec("COUNT", "count", 2, EvidenceMode.TRIGGER_TRANSACTION),
        "B001",
        date(2026, 4, 30),
    )

    assert result.evidence_transaction_id == "A003"
    assert result.status == "success"


def test_prohibited_existence_returns_first_matching_transaction(
    service: EvaluationService,
) -> None:
    covenant = spec(
        "BAN",
        "existence",
        0,
        EvidenceMode.VIOLATING_TRANSACTION,
        filters=[FilterSpec(field="counterparty_id", operator="eq", value="BANNED")],
    )
    covenant.condition.comparator = "=="

    result = service.evaluate(covenant, "B001", date(2026, 4, 30))

    assert result.evidence_transaction_id == "A004"


def test_frequency_trigger_comes_from_first_violating_bucket(
    service: EvaluationService,
) -> None:
    result = service.evaluate(
        spec("FREQ", "frequency", 3, EvidenceMode.TRIGGER_TRANSACTION),
        "B001",
        date(2026, 4, 30),
    )

    assert result.number == 4
    assert result.evidence_transaction_id == "A004"


def test_max_tie_break_is_stable_by_date_then_id(service: EvaluationService) -> None:
    result = service.evaluate(
        spec("MAX", "max", 35, EvidenceMode.MAX_TRANSACTION),
        "B001",
        date(2026, 4, 30),
    )

    assert result.evidence_transaction_id == "A004"
