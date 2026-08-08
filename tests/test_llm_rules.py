from __future__ import annotations

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
