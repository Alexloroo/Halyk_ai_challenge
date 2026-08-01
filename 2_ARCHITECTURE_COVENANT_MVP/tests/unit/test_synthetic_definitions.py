from collections import Counter
from decimal import Decimal

from halyk_covenants.synthetic import build_synthetic_definition


def test_definition_covers_documents_workbook_entities_and_supported_metrics() -> None:
    definition = build_synthetic_definition()

    assert len(definition.documents) == 2
    assert len(definition.borrowers) == 3
    assert len(definition.covenants) == 8
    assert len(definition.cases) == 10
    assert {"sum", "count", "max", "min", "avg"} == {
        spec.metric.metric_type for spec in definition.covenants
    }


def test_definition_references_are_unique_and_resolvable() -> None:
    definition = build_synthetic_definition()
    covenant_ids = [covenant.covenant_id for covenant in definition.covenants]
    case_ids = [case.case_id for case in definition.cases]
    borrower_ids = {borrower.borrower_id for borrower in definition.borrowers}
    document_files = {document.file_name for document in definition.documents}

    assert len(covenant_ids) == len(set(covenant_ids))
    assert len(case_ids) == len(set(case_ids))
    assert all(case.covenant_id in covenant_ids for case in definition.cases)
    assert all(case.borrower_id in borrower_ids for case in definition.cases)
    assert all(case.document_file in document_files for case in definition.cases)


def test_expected_answers_are_hand_checkable_and_expose_trigger_evidence_gap() -> None:
    definition = build_synthetic_definition()
    answers = {case.case_id: case.expected for case in definition.cases}

    assert answers["ALPHA-SUM-APRIL"].number == Decimal("16000000.000000")
    assert answers["ALPHA-SUM-APRIL"].verdict == "violated"
    assert answers["ALPHA-MAX-APRIL"].evidence_transaction_id == "A002"
    assert answers["ALPHA-COUNT-TRIGGER"].number == 3
    assert answers["ALPHA-COUNT-TRIGGER"].evidence_transaction_id == "A003"
    assert answers["BETA-AVG-APRIL"].number == Decimal("4000000.000000")
    assert answers["ALPHA-SUM-EMPTY"].number == Decimal("0.000000")
    assert answers["ALPHA-MAX-EMPTY"].number is None
    assert answers["GAMMA-SUM-DUPLICATE"].number == Decimal("7000000.000000")


def test_definition_contains_one_exact_duplicate_and_preserves_leading_zero_ids() -> None:
    definition = build_synthetic_definition()
    transaction_rows = [
        tuple(transaction.model_dump(mode="json").values())
        for transaction in definition.transactions
    ]
    duplicate_groups = [count for count in Counter(transaction_rows).values() if count > 1]

    assert duplicate_groups == [2]
    assert "000777" in {borrower.borrower_id for borrower in definition.borrowers}
    assert "000003" in {transaction.transaction_id for transaction in definition.transactions}


def test_all_document_defects_are_explicitly_declared() -> None:
    definition = build_synthetic_definition()

    assert all(document.defects for document in definition.documents)
    assert sum(len(document.defects) for document in definition.documents) >= 10
    assert all("synthetic" in borrower.identifiers for borrower in definition.borrowers)
