from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from halyk_covenants.domain import (
    Calculation,
    ConditionSpec,
    CovenantSpec,
    DocumentBlock,
    MetricSpec,
    PageExtractionQuality,
    PipelineStageRecord,
    SourceRef,
)


def base_spec(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "covenant_id": "COV-GROUP",
        "raw_text": "Combined outgoing payments shall not exceed 50M KZT.",
        "borrower_ids": ["B1", "B2"],
        "metric": {"metric_type": "sum", "field": "amount"},
        "condition": {"comparator": "<=", "threshold": "50000000", "currency": "KZT"},
        "scope_mode": "group",
        "source": {"document_id": "DOC1", "page": 1},
        "confidence": 1,
    }
    payload.update(overrides)
    return payload


def test_group_covenant_requires_multiple_borrowers() -> None:
    with pytest.raises(ValidationError):
        CovenantSpec.model_validate(base_spec(borrower_ids=["B1"]))


def test_extended_covenant_defaults_preserve_old_specs() -> None:
    spec = CovenantSpec.model_validate(base_spec(scope_mode="per_borrower"))

    assert spec.covenant_group_id is None
    assert spec.group_by == []
    assert spec.exclusions == []
    assert spec.date_field == "transaction_date"
    assert spec.status == "compiled"


def test_document_and_audit_models_are_strict_and_decimal_safe() -> None:
    source = SourceRef(document_id="DOC1", page=1, bbox=(1, 2, 3, 4))
    block = DocumentBlock(
        block_id="BLK1",
        document_id="DOC1",
        page=1,
        block_type="text",
        text="Лимит 5 000 000 KZT",
        bbox=(1, 2, 3, 4),
        extraction_method="ocr",
        confidence=Decimal("0.99"),
        source=source,
    )
    quality = PageExtractionQuality(
        native_text_chars=0,
        text_density=0,
        image_count=1,
        table_count=0,
        route="ocr",
        confidence=Decimal("0.95"),
    )
    calculation = Calculation(
        calculation_id="CALC1",
        covenant_id="COV1",
        borrower_ids=["B1"],
        metric_type="sum",
        value=Decimal("1.100000"),
        input_row_count=1,
    )
    stage = PipelineStageRecord(
        run_id="RUN1",
        stage_name="ocr.paddle_gpu",
        status="success",
        started_at=datetime(2026, 8, 2, tzinfo=UTC),
        finished_at=datetime(2026, 8, 2, tzinfo=UTC),
    )

    assert block.confidence == Decimal("0.99")
    assert quality.route == "ocr"
    assert calculation.value == Decimal("1.100000")
    assert stage.stage_name == "ocr.paddle_gpu"


def test_group_by_rejects_unknown_transaction_fields() -> None:
    with pytest.raises(ValidationError):
        CovenantSpec.model_validate(base_spec(group_by=["drop table transactions"]))


def test_condition_remains_decimal_exact() -> None:
    condition = ConditionSpec(comparator="<=", threshold="0.30", unit="ratio")
    metric = MetricSpec(
        metric_type="ratio",
        numerator=MetricSpec(metric_type="sum", field="amount"),
        denominator=MetricSpec(metric_type="sum", field="amount"),
    )

    assert condition.threshold == Decimal("0.30")
    assert metric.numerator is not None
