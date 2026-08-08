from __future__ import annotations

from halyk.categorize import Category
from halyk.llm_rules import (
    RuleExtractionRequest,
    RuleExtractionSpec,
    _validate_and_build,
)
from halyk.rules import RuleKind


def test_documentary_missing_rule_can_be_recovered_without_numeric_threshold() -> None:
    agreement = (
        "Clause 6-4. Insurance maintenance. "
        "The Borrower shall maintain a valid property insurance policy at all times."
    )
    request = RuleExtractionRequest(
        key="N1/6.4",
        scenario_id="N1",
        clause="6.4",
        agreement_text=agreement,
    )
    resolution = RuleExtractionSpec(
        clause="6.4",
        heading_evidence="Clause 6-4. Insurance maintenance.",
        rule_evidence=(
            "The Borrower shall maintain a valid property insurance policy at all times."
        ),
        kind=RuleKind.UNKNOWN,
        comparator=">=",
        categories=[],
        is_documentary=True,
    )

    errors, rule = _validate_and_build(resolution, request)

    assert errors == []
    assert rule is not None
    assert rule.threshold is None
    assert rule.clause == "6.4"


def test_numeric_missing_rule_still_requires_supported_threshold() -> None:
    agreement = "Clause 6-5. Liquidity. The Borrower shall maintain adequate liquidity."
    request = RuleExtractionRequest(
        key="N1/6.5",
        scenario_id="N1",
        clause="6.5",
        agreement_text=agreement,
    )
    resolution = RuleExtractionSpec(
        clause="6.5",
        heading_evidence="Clause 6-5. Liquidity.",
        rule_evidence="The Borrower shall maintain adequate liquidity.",
        kind=RuleKind.UNKNOWN,
        comparator=">=",
        categories=[],
        is_documentary=False,
    )

    errors, rule = _validate_and_build(resolution, request)

    assert rule is None
    assert "rule_evidence contains no supported threshold" in errors


def test_unknown_category_from_llm_does_not_block_unknown_clause_recovery() -> None:
    agreement = (
        "Section 5.1 Springing transfer limitation. "
        "If leverage exceeds 2.50x, transfers must not exceed USD 500,000."
    )
    request = RuleExtractionRequest(
        key="J4/5.1",
        scenario_id="J4",
        clause="5.1",
        agreement_text=agreement,
    )
    resolution = RuleExtractionSpec(
        clause="5.1",
        heading_evidence="Section 5.1 Springing transfer limitation.",
        rule_evidence="If leverage exceeds 2.50x, transfers must not exceed USD 500,000.",
        kind=RuleKind.UNKNOWN,
        comparator="<=",
        categories=[Category.UNKNOWN],
    )

    errors, rule = _validate_and_build(resolution, request)

    assert errors == []
    assert rule is not None
    assert rule.categories == frozenset()


def test_new_semantic_category_does_not_block_missing_clause_recovery() -> None:
    agreement = (
        "Section 5.2 Debt service. Scheduled debt principal repayments must not exceed USD 500,000."
    )
    request = RuleExtractionRequest(
        key="J4/5.2",
        scenario_id="J4",
        clause="5.2",
        agreement_text=agreement,
    )
    resolution = RuleExtractionSpec(
        clause="5.2",
        heading_evidence="Section 5.2 Debt service.",
        rule_evidence="Scheduled debt principal repayments must not exceed USD 500,000.",
        kind=RuleKind.UNKNOWN,
        comparator="<=",
        categories=[Category.DEBT_PRINCIPAL],
    )

    errors, rule = _validate_and_build(resolution, request)

    assert errors == []
    assert rule is not None
    assert rule.categories == frozenset({Category.DEBT_PRINCIPAL})
