from decimal import Decimal

from halyk_covenants.domain import CovenantResult
from halyk_covenants.submission import (
    SubmissionProfile,
    SubmissionSerializer,
    SubmissionValidator,
)


def profile() -> SubmissionProfile:
    return SubmissionProfile(
        name="synthetic",
        ratio_representation="percentage",
        verdict_labels={"complied": "COMPLIED", "violated": "VIOLATED", "unknown": "UNKNOWN"},
    )


def result(
    borrower_id: str,
    covenant_id: str,
    number: Decimal | int | None,
    *,
    unit: str | None = None,
) -> CovenantResult:
    return CovenantResult(
        borrower_id=borrower_id,
        covenant_id=covenant_id,
        verdict="violated",
        number=number,
        number_unit=unit,
        status="success" if number is not None else "failed",
    )


def test_synthetic_profile_serializes_ratio_as_percentage() -> None:
    payload = SubmissionSerializer(profile()).serialize(
        [result("B1", "RATIO", Decimal("0.34"), unit="ratio")]
    )

    assert payload["answers"][0]["number"] == "34"


def test_serializer_orders_by_borrower_then_covenant_and_keeps_null() -> None:
    payload = SubmissionSerializer(profile()).serialize(
        [result("B2", "C2", 2), result("B1", "C9", None), result("B1", "C1", 1)]
    )

    assert [(answer["borrower_id"], answer["covenant_id"]) for answer in payload["answers"]] == [
        ("B1", "C1"),
        ("B1", "C9"),
        ("B2", "C2"),
    ]
    assert payload["answers"][1]["number"] is None


def test_strict_validator_rejects_extra_keys() -> None:
    report = SubmissionValidator(profile()).validate({"answers": [], "unexpected": True})

    assert report.valid is False
    assert "unexpected" in report.errors[0]


def test_serializer_output_passes_strict_validator() -> None:
    payload = SubmissionSerializer(profile()).serialize([result("B1", "C1", Decimal("10"))])

    assert SubmissionValidator(profile()).validate(payload).valid is True
