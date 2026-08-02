from decimal import Decimal

from halyk_covenants.covenants import CovenantDetector
from halyk_covenants.domain import DocumentBlock, SourceRef


def block(text: str) -> DocumentBlock:
    return DocumentBlock(
        block_id="b1",
        document_id="contract",
        page=2,
        block_type="text",
        text=text,
        extraction_method="native",
        confidence=Decimal("0.99"),
        source=SourceRef(document_id="contract", page=2),
    )


def test_detector_splits_two_independently_scored_conditions() -> None:
    candidates = CovenantDetector().detect(
        [
            block(
                "Monthly outgoing payments must not exceed 10,000,000 KZT, "
                "and no more than 5 payments above 1,000,000 KZT are permitted."
            )
        ]
    )

    assert [candidate.ordinal for candidate in candidates] == [1, 2]
    assert all(candidate.source.page == 2 for candidate in candidates)
    assert "10,000,000" in candidates[0].raw_text
    assert "no more than 5" in candidates[1].raw_text


def test_detector_ignores_ordinary_descriptive_text() -> None:
    assert CovenantDetector().detect([block("The borrower was founded in 2012.")]) == []


def test_detector_deduplicates_narrative_and_table_copy_by_covenant_code() -> None:
    narrative = block(
        "7.1 Monthly outgoing payments must not exceed 10,000,000 KZT. [COV-ALPHA-SUM]"
    )
    table_copy = block("COV-ALPHA-SUM SUM outgoing <= 10,000,000 KZT")
    table_copy.block_id = "b2"

    candidates = CovenantDetector().detect([narrative, table_copy])

    assert len(candidates) == 1
    assert candidates[0].raw_text == narrative.text
