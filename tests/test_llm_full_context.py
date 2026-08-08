from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from halyk.categorize import Category
from halyk.docs import DocKind, Document, Edition
from halyk.evaluate import EvaluationTrace, evaluate
from halyk.generic_formula import CovenantMode, ExternalMetric, GenericFormulaSpec
from halyk.ledger import LedgerEntry
from halyk.llm_capabilities import CapabilityResult, EvidenceCandidate
from halyk.llm_extract import AggKind, FormulaSpec, OutputKind
from halyk.llm_full_context import (
    FullContextCalculation,
    FullContextEvidence,
    FullContextRequest,
    FullContextResult,
    FullContextStep,
    FullContextVerification,
    build_full_context_payload,
    canonicalize_full_context_calculation,
    resolve_full_context,
    validate_full_context_calculation,
    validate_full_context_verification,
)
from halyk.rules import Rule, RuleKind
from halyk.run import _full_context_reason, solve
from halyk.tracing import TraceWriter


def _entry(
    txn_id: str,
    amount: str,
    category: Category,
    *,
    day: date = date(2025, 6, 1),
    account_id: str = "ACC-5100",
) -> LedgerEntry:
    return LedgerEntry(
        txn_id=txn_id,
        scenario_id="X51",
        day=day,
        account_id=account_id,
        counterparty="Counterparty LLP",
        description="scenario transaction",
        amount=Decimal(amount),
        currency="USD",
        category=category,
    )


def _request() -> FullContextRequest:
    rule = Rule(
        scenario_id="X51",
        clause="6.1",
        heading="Complex promotion intensity",
        text="Promotion costs divided by revenue must not exceed 0.40x.",
        kind=RuleKind.UNKNOWN,
        comparator="<=",
        threshold=Decimal("0.40"),
        period=(date(2025, 1, 1), date(2025, 12, 31)),
        categories=frozenset({Category.MARKETING, Category.REVENUE}),
    )
    return FullContextRequest(
        key="X51/6.1",
        rule=rule,
        account_id="ACC-5100",
        agreement_text="Current agreement definitions and the complete clause.",
        ledger=(
            _entry("TXN-X51-001", "-835000", Category.MARKETING),
            _entry("TXN-X51-004", "2000000", Category.REVENUE),
        ),
        audit_adjustments=(),
        candidates=(
            EvidenceCandidate(
                candidate_id="candidate-002",
                source="statement.pdf",
                text="Total debt at year end was USD 2,000,000",
            ),
        ),
        external_metrics={
            "total_debt": ExternalMetric(
                name="total_debt",
                value=Decimal("2000000"),
                source_document="statement.pdf",
                evidence="Total debt at year end was USD 2,000,000",
                value_text="2,000,000",
            )
        },
        kyc_text="KYC for ACC-5100 only",
    )


def _calculation() -> FullContextCalculation:
    return FullContextCalculation(
        actual=Decimal("0.4175"),
        status="BREACH",
        comparator="<=",
        threshold=Decimal("0.40"),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        calculation_steps=[
            FullContextStep(
                operation="sum",
                inputs=["txn:TXN-X51-001"],
                input_mode="magnitude",
                result=Decimal("835000"),
            ),
            FullContextStep(
                operation="sum",
                inputs=["txn:TXN-X51-004"],
                input_mode="magnitude",
                result=Decimal("2000000"),
            ),
            FullContextStep(
                operation="divide",
                inputs=["step:1", "step:2"],
                input_mode="signed",
                result=Decimal("0.4175"),
            ),
        ],
        used_txn_ids=["TXN-X51-001", "TXN-X51-004"],
        document_evidence=[],
        reasoning_summary="Marketing magnitude divided by revenue.",
    )


def test_full_context_payload_is_scenario_scoped_and_contains_no_leakage_fields() -> None:
    payload = build_full_context_payload(_request())

    assert payload["scenario_id"] == "X51"
    assert {row["txn_id"] for row in payload["ledger"]} == {
        "TXN-X51-001",
        "TXN-X51-004",
    }
    serialized = str(payload).casefold()
    assert "ground_truth" not in serialized
    assert "scoring" not in serialized
    assert "synthetic manifest" not in serialized
    assert "expected status" not in serialized


def test_python_recomputes_every_full_context_step_and_status() -> None:
    assert validate_full_context_calculation(_calculation(), _request()) == []


def test_trailing_reconciliation_steps_are_removed_after_complete_actual() -> None:
    calculation = _calculation().model_copy(deep=True)
    calculation.calculation_steps.append(
        FullContextStep(
            operation="add",
            inputs=["step:3", "txn:TXN-X51-001"],
            input_mode="magnitude",
            result=Decimal("0.4175"),
        )
    )

    canonical = canonicalize_full_context_calculation(calculation)

    assert len(canonical.calculation_steps) == 3
    assert validate_full_context_calculation(canonical, _request()) == []


def test_canonicalization_recomputes_model_arithmetic_and_financial_sign_mode() -> None:
    request = _request()
    request.rule.text = "Financing proceeds reduced by interest must be at least 700000."
    request.rule.comparator = ">="
    request.rule.threshold = Decimal("700000")
    request.rule.categories = frozenset({Category.FINANCING, Category.INTEREST})
    request = replace(
        request,
        ledger=(
            _entry("TXN-X51-001", "1000000", Category.FINANCING),
            _entry("TXN-X51-004", "-200000", Category.INTEREST),
        ),
    )
    calculation = FullContextCalculation(
        actual=Decimal("1000000"),
        status="COMPLIANT",
        comparator=">=",
        threshold=Decimal("700000"),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        calculation_steps=[
            FullContextStep(
                operation="sum",
                inputs=["txn:TXN-X51-001"],
                input_mode="signed",
                result=Decimal("1000000"),
            ),
            FullContextStep(
                operation="subtract",
                inputs=["step:1", "txn:TXN-X51-004"],
                input_mode="signed",
                result=Decimal("800000"),
            ),
        ],
        used_txn_ids=["TXN-X51-001", "TXN-X51-004"],
        document_evidence=[],
        reasoning_summary="Financing less paid interest.",
    )

    canonical = canonicalize_full_context_calculation(calculation, request)

    assert canonical.calculation_steps[1].input_mode == "magnitude"
    assert canonical.calculation_steps[1].result == Decimal("800000")
    assert canonical.actual == Decimal("800000")
    assert canonical.status == "COMPLIANT"
    assert validate_full_context_calculation(canonical, request) == []


def test_conditional_calculation_keeps_primary_actual_when_later_step_checks_proviso() -> None:
    request = _request()
    request.rule.text = "Rent above $1,000,000 is not a default if insurance is at least $200,000."
    request.rule.threshold = Decimal("1000000")
    request = replace(
        request,
        ledger=(
            _entry("TXN-X51-001", "-1200000", Category.LEASE),
            _entry("TXN-X51-004", "-250000", Category.INSURANCE),
        ),
    )
    calculation = FullContextCalculation(
        actual=Decimal("1200000"),
        status="COMPLIANT",
        comparator="<=",
        threshold=Decimal("1000000"),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        calculation_steps=[
            FullContextStep(
                operation="sum",
                inputs=["txn:TXN-X51-001"],
                input_mode="magnitude",
                result=Decimal("1200000"),
            ),
            FullContextStep(
                operation="sum",
                inputs=["txn:TXN-X51-004"],
                input_mode="magnitude",
                result=Decimal("250000"),
            ),
        ],
        used_txn_ids=["TXN-X51-001", "TXN-X51-004"],
        document_evidence=[],
        reasoning_summary="Insurance proviso is satisfied.",
    )

    canonical = canonicalize_full_context_calculation(calculation, request)

    assert canonical.actual == Decimal("1200000")
    assert validate_full_context_calculation(canonical, request) == []


def test_canonicalization_repairs_safe_transaction_and_decimal_prefixes() -> None:
    request = _request()
    calculation = _calculation().model_copy(deep=True)
    calculation.calculation_steps[0].inputs = ["txn:X51-001"]
    calculation.calculation_steps[1].inputs = ["txn:X51-004"]
    calculation.used_txn_ids = ["txn:X51-001", "txn:X51-004"]

    canonical = canonicalize_full_context_calculation(calculation, request)

    assert canonical.used_txn_ids == ["TXN-X51-001", "TXN-X51-004"]
    assert canonical.calculation_steps[0].inputs == ["txn:TXN-X51-001"]
    assert validate_full_context_calculation(canonical, request) == []


def test_conditional_full_context_can_select_an_explicit_obligation_threshold() -> None:
    request = _request()
    request.rule.text = (
        "If capital expenditure exceeds $1,500,000, insurance premiums must be at least $250,000."
    )
    request.rule.threshold = Decimal("1500000")
    request = replace(
        request,
        ledger=(_entry("TXN-X51-001", "-300000", Category.INSURANCE),),
    )
    calculation = _calculation().model_copy(deep=True)
    calculation.actual = Decimal("300000")
    calculation.status = "COMPLIANT"
    calculation.comparator = ">="
    calculation.threshold = Decimal("250000")
    calculation.calculation_steps = [
        FullContextStep(
            operation="sum",
            inputs=["txn:TXN-X51-001"],
            input_mode="magnitude",
            result=Decimal("300000"),
        )
    ]
    calculation.used_txn_ids = ["TXN-X51-001"]

    assert validate_full_context_calculation(calculation, request) == []


def test_full_context_rejects_wrong_arithmetic_and_out_of_period_transaction() -> None:
    request = _request()
    bad_entry = _entry(
        "TXN-X51-999",
        "-1",
        Category.MARKETING,
        day=date(2024, 12, 31),
    )
    request = FullContextRequest(**{**request.__dict__, "ledger": (*request.ledger, bad_entry)})
    calculation = _calculation().model_copy(deep=True)
    calculation.calculation_steps[0].result = Decimal("800000")
    calculation.used_txn_ids.append("TXN-X51-999")

    errors = validate_full_context_calculation(calculation, request)

    assert "step 1 result does not match Python arithmetic" in errors
    assert "used transaction is outside covenant period: TXN-X51-999" in errors


def test_full_context_rejects_invented_document_quote_and_status() -> None:
    calculation = _calculation().model_copy(deep=True)
    calculation.status = "COMPLIANT"
    calculation.document_evidence = [
        FullContextEvidence(
            candidate_id="candidate-002",
            quote="Invented debt disclosure",
        )
    ]

    errors = validate_full_context_calculation(calculation, _request())

    assert "status contradicts actual, comparator, and threshold" in errors
    assert "document evidence is not an exact candidate quote: candidate-002" in errors


def test_document_quote_allows_only_pdf_whitespace_normalization() -> None:
    request = _request()
    request = replace(
        request,
        candidates=(
            EvidenceCandidate(
                candidate_id="candidate-002",
                source="statement.pdf",
                text="Total debt at year end was\nUSD 2,000,000",
            ),
        ),
    )
    calculation = _calculation().model_copy(deep=True)
    calculation.document_evidence = [
        FullContextEvidence(
            candidate_id="candidate-002",
            quote="Total debt at year end was USD 2,000,000",
        )
    ]

    assert validate_full_context_calculation(calculation, request) == []


def test_document_quote_ignores_standalone_pdf_page_number() -> None:
    request = _request()
    request = replace(
        request,
        candidates=(
            EvidenceCandidate(
                candidate_id="candidate-002",
                source="statement.pdf",
                text="Net Debt means debt less cash\n4\nand EBITDA means revenue less expenses.",
            ),
        ),
        external_metrics={},
    )
    calculation = _calculation().model_copy(deep=True)
    calculation.document_evidence = [
        FullContextEvidence(
            candidate_id="candidate-002",
            quote="Net Debt means debt less cash and EBITDA means revenue less expenses.",
        )
    ]

    assert validate_full_context_calculation(calculation, request) == []


def test_independent_verifier_must_match_calculator_sources_and_result() -> None:
    calculation = _calculation()
    accepted = FullContextVerification(
        accepted=True,
        actual=Decimal("0.4175"),
        status="BREACH",
        used_txn_ids=["TXN-X51-001", "TXN-X51-004"],
        document_candidate_ids=[],
        issues=[],
    )
    mismatch = accepted.model_copy(update={"actual": Decimal("0.40")})

    assert validate_full_context_verification(accepted, calculation) == []
    assert validate_full_context_verification(mismatch, calculation) == [
        "verifier actual differs from calculator"
    ]


def test_verifier_confirmation_is_accepted_when_only_compound_actual_format_is_disputed() -> None:
    calculation = _calculation()
    verification = FullContextVerification(
        accepted=False,
        actual=calculation.actual,
        status=calculation.status,
        used_txn_ids=calculation.used_txn_ids,
        document_candidate_ids=[],
        issues=[
            "The status is correct and the calculation is correct, but actual reports "
            "the primary metric rather than a joint metric."
        ],
    )

    assert validate_full_context_verification(verification, calculation) == []


def test_verifier_rejection_remains_authoritative_for_missing_trigger_data() -> None:
    calculation = _calculation()
    verification = FullContextVerification(
        accepted=False,
        actual=calculation.actual,
        status=calculation.status,
        used_txn_ids=calculation.used_txn_ids,
        document_candidate_ids=[],
        issues=["The conditional trigger cannot be established from supplied evidence."],
    )

    assert validate_full_context_verification(verification, calculation) == [
        "independent verifier rejected calculation"
    ]


def test_conditional_compliant_is_accepted_when_verifier_confirms_primary_amount() -> None:
    request = _request()
    request.rule.text = (
        "If an unavailable leverage ratio exceeds 3.00x, marketing payments must not "
        "exceed $835,000.00. While it does not exceed 3.00x, the limit does not apply."
    )
    calculation = _calculation().model_copy(
        update={"actual": Decimal("835000"), "status": "COMPLIANT"}, deep=True
    )
    calculation.calculation_steps = calculation.calculation_steps[:1]
    calculation.used_txn_ids = ["TXN-X51-001"]
    verification = FullContextVerification(
        accepted=False,
        actual=calculation.actual,
        status=calculation.status,
        used_txn_ids=calculation.used_txn_ids,
        document_candidate_ids=[],
        issues=[
            "The conditional trigger cannot be established from supplied evidence. "
            "The proposal's actual amount is correctly computed from TXN-X51-001, but "
            "the status rests on the unavailable trigger."
        ],
    )

    assert validate_full_context_verification(verification, calculation, request) == []


def test_conditional_breach_requires_a_separate_trigger_calculation() -> None:
    request = _request()
    request.rule.text = "If leverage exceeds 3.00x, marketing payments must not exceed $800,000.00."
    calculation = _calculation().model_copy(
        update={"actual": Decimal("835000"), "status": "BREACH"}, deep=True
    )
    calculation.calculation_steps = calculation.calculation_steps[:1]
    calculation.used_txn_ids = ["TXN-X51-001"]

    assert "conditional breach has no independently calculated trigger" in (
        validate_full_context_calculation(calculation, request)
    )


def test_unavailable_conditional_trigger_preserves_amount_but_disables_limit() -> None:
    request = _request()
    request.rule.text = (
        "If an unavailable leverage ratio exceeds 3.00x, marketing payments must not "
        "exceed $800,000.00."
    )
    calculation = _calculation().model_copy(
        update={
            "actual": Decimal("835000"),
            "status": "BREACH",
            "reasoning_summary": (
                "The supplied data does not provide the leverage ratio, so the trigger "
                "cannot be independently established."
            ),
        },
        deep=True,
    )
    calculation.calculation_steps = calculation.calculation_steps[:1]
    calculation.used_txn_ids = ["TXN-X51-001"]

    canonical = canonicalize_full_context_calculation(calculation, request)

    assert canonical.actual == Decimal("835000")
    assert canonical.status == "COMPLIANT"
    assert validate_full_context_calculation(canonical, request) == []

    verification = FullContextVerification(
        accepted=False,
        actual=canonical.actual,
        status="BREACH",
        used_txn_ids=canonical.used_txn_ids,
        document_candidate_ids=[],
        issues=["The primary amount exceeds the cap."],
    )
    assert validate_full_context_verification(verification, canonical, request) == []


def test_springing_net_leverage_trigger_is_rebuilt_from_defined_ledger_metrics() -> None:
    request = _request()
    request.rule.text = (
        "When the Net Leverage Ratio exceeds 2.50x, transfers to an Unrestricted "
        "Subsidiary must not exceed $500.00."
    )
    request.rule.threshold = Decimal("500")
    request.rule.comparator = ">="
    request.rule.categories = frozenset({Category.ASSET_TRANSFER})
    transfer = _entry("TXN-X51-001", "-600", Category.ASSET_TRANSFER)
    transfer.is_unrestricted_transfer = True
    request = replace(
        request,
        agreement_text=(
            "Net Leverage Ratio means Net Debt divided by EBITDA. Net Debt means financing "
            "drawn less scheduled principal repayments. EBITDA means Revenue less Operating "
            "Expenses."
        ),
        ledger=(
            transfer,
            _entry("TXN-X51-002", "900", Category.FINANCING),
            _entry("TXN-X51-003", "-100", Category.DEBT_PRINCIPAL),
            _entry("TXN-X51-004", "500", Category.REVENUE),
            _entry("TXN-X51-005", "-200", Category.OPEX),
        ),
        external_metrics={},
    )
    calculation = FullContextCalculation(
        actual=Decimal("600"),
        status="BREACH",
        comparator="<=",
        threshold=Decimal("500"),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        calculation_steps=[
            FullContextStep(
                operation="sum",
                inputs=["txn:TXN-X51-001"],
                input_mode="magnitude",
                result=Decimal("600"),
            )
        ],
        used_txn_ids=["TXN-X51-001"],
        document_evidence=[],
        reasoning_summary="Incomplete model calculation.",
    )

    canonical = canonicalize_full_context_calculation(calculation, request)

    assert canonical.actual == Decimal("600")
    assert canonical.status == "BREACH"
    assert canonical.calculation_steps[-1].result == Decimal("800") / Decimal("300")
    assert validate_full_context_calculation(canonical, request) == []


def test_conditional_insurance_actual_is_rebuilt_from_insurance_not_capex() -> None:
    request = _request()
    request.rule.text = (
        "If aggregate Capital Expenditure exceeds $1,500,000.00, the Borrower must "
        "maintain Insurance premiums of at least $250,000.00."
    )
    request.rule.threshold = Decimal("250000")
    request.rule.comparator = ">="
    request.rule.categories = frozenset({Category.CAPEX, Category.INSURANCE})
    request = replace(
        request,
        ledger=(
            _entry("TXN-X51-001", "-1831339.05", Category.CAPEX),
            _entry("TXN-X51-004", "-284617.42", Category.INSURANCE),
        ),
        external_metrics={},
    )
    calculation = _calculation().model_copy(
        update={"actual": Decimal("1831339.05"), "status": "COMPLIANT"}, deep=True
    )
    calculation.calculation_steps = [
        FullContextStep(
            operation="sum",
            inputs=["txn:TXN-X51-001"],
            input_mode="magnitude",
            result=Decimal("1831339.05"),
        )
    ]
    calculation.used_txn_ids = ["TXN-X51-001"]

    canonical = canonicalize_full_context_calculation(calculation, request)

    assert canonical.actual == Decimal("284617.42")
    assert canonical.status == "COMPLIANT"
    assert validate_full_context_calculation(canonical, request) == []


def test_adjusted_debt_with_guarantee_uses_ledger_ebitda() -> None:
    request = _request()
    request.rule.text = (
        "Adjusted debt leverage must not exceed 3.00x. Adjusted total debt includes "
        "financing drawn plus all guarantees, and EBITDA means Revenue less Operating expenses."
    )
    request.rule.threshold = Decimal("3.00")
    request.rule.comparator = "<="
    request.rule.categories = frozenset({Category.OPEX, Category.REVENUE})
    request = replace(
        request,
        ledger=(
            _entry("TXN-X51-001", "7212928.03", Category.FINANCING),
            _entry("TXN-X51-002", "9358965.30", Category.REVENUE),
            _entry("TXN-X51-003", "-6399342.83", Category.OPEX),
        ),
        candidates=(
            EvidenceCandidate(
                candidate_id="candidate-004",
                source="audit.pdf",
                text="Guarantees outstanding at period end were $1,743,286.47.",
            ),
        ),
        external_metrics={},
    )
    calculation = _calculation().model_copy(deep=True)
    calculation.document_evidence = [
        FullContextEvidence(
            candidate_id="candidate-004",
            quote="Guarantees outstanding at period end were $1,743,286.47.",
        )
    ]

    canonical = canonicalize_full_context_calculation(calculation, request)

    assert canonical.actual.quantize(Decimal("0.000001")) == Decimal("3.026134")
    assert canonical.status == "BREACH"
    assert validate_full_context_calculation(canonical, request) == []


def test_full_context_rejects_zero_placeholder_when_reasoning_admits_missing_trigger() -> None:
    request = _request()
    request.rule.text = "If a missing group ratio exceeds 3.4x, distributions are capped."
    calculation = _calculation().model_copy(deep=True)
    calculation.actual = Decimal(0)
    calculation.status = "COMPLIANT"
    calculation.calculation_steps = [
        FullContextStep(operation="sum", inputs=["0"], input_mode="signed", result=Decimal(0))
    ]
    calculation.used_txn_ids = []
    calculation.reasoning_summary = (
        "No group metrics are supplied, so the trigger condition cannot be established "
        "from supplied evidence and actual is reported as zero."
    )

    assert "calculation admits that required trigger data is unavailable" in (
        validate_full_context_calculation(calculation, request)
    )


def test_python_enforces_satisfied_insurance_proviso() -> None:
    request = _request()
    request.rule.text = (
        "Rent above $1,000,000 does not itself cause a breach if insurance premiums "
        "are at least $200,000. Both conditions are tested together."
    )
    request.rule.threshold = Decimal("1000000")
    request.rule.categories = frozenset({Category.LEASE, Category.INSURANCE})
    request = replace(
        request,
        ledger=(
            _entry("TXN-X51-001", "-1200000", Category.LEASE),
            _entry("TXN-X51-004", "-250000", Category.INSURANCE),
        ),
    )
    calculation = FullContextCalculation(
        actual=Decimal("1200000"),
        status="BREACH",
        comparator="<=",
        threshold=Decimal("1000000"),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        calculation_steps=[
            FullContextStep(
                operation="sum",
                inputs=["txn:TXN-X51-001"],
                input_mode="magnitude",
                result=Decimal("1200000"),
            )
        ],
        used_txn_ids=["TXN-X51-001"],
        document_evidence=[],
        reasoning_summary="Rent exceeds the cap.",
    )

    errors = validate_full_context_calculation(calculation, request)

    assert "status contradicts the satisfied minimum proviso" in errors
    assert "calculation omits transactions used by the minimum proviso" in errors


def test_capped_adjusted_ebitda_is_rebuilt_as_python_verifiable_steps() -> None:
    request = _request()
    request.rule.text = (
        "Total debt means financing proceeds. Adjusted EBITDA means Revenue less Operating "
        "expenses plus restructuring items added back by auditors, provided that aggregate "
        "add-backs do not exceed 5% of Revenue. The ratio must not exceed 3.00x."
    )
    request.rule.threshold = Decimal("3.00")
    request.rule.categories = frozenset({Category.OPEX, Category.REVENUE})
    request = replace(
        request,
        ledger=(
            _entry("TXN-X51-001", "9617432.88", Category.FINANCING),
            _entry("TXN-X51-002", "8240517.36", Category.REVENUE),
            _entry("TXN-X51-003", "-4500060.59", Category.OPEX),
            _entry("TXN-X51-004", "-690314.22", Category.OPEX),
        ),
        candidates=(
            EvidenceCandidate(
                candidate_id="candidate-002",
                source="audit.pdf",
                text=(
                    "Restructuring item added back by auditors: $690,314.22. "
                    "Only items of at least $500,000 qualify."
                ),
            ),
        ),
        external_metrics={},
    )
    calculation = FullContextCalculation(
        actual=Decimal(0),
        status="COMPLIANT",
        comparator="<=",
        threshold=Decimal("3.00"),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        calculation_steps=[
            FullContextStep(operation="sum", inputs=["0"], input_mode="signed", result=Decimal(0))
        ],
        used_txn_ids=[],
        document_evidence=[
            FullContextEvidence(
                candidate_id="candidate-002",
                quote="Restructuring item added back by auditors: $690,314.22.",
            )
        ],
        reasoning_summary="The model emitted an unusable placeholder.",
    )

    canonical = canonicalize_full_context_calculation(calculation, request)

    assert canonical.actual.quantize(Decimal("0.000001")) == Decimal("2.777864")
    assert canonical.status == "COMPLIANT"
    assert canonical.used_txn_ids == [
        "TXN-X51-001",
        "TXN-X51-002",
        "TXN-X51-003",
        "TXN-X51-004",
    ]
    assert validate_full_context_calculation(canonical, request) == []


def test_verifier_may_use_additional_supplied_document_context() -> None:
    calculation = _calculation().model_copy(deep=True)
    calculation.document_evidence = [
        FullContextEvidence(candidate_id="candidate-002", quote="exact")
    ]
    verification = FullContextVerification(
        accepted=True,
        actual=calculation.actual,
        status=calculation.status,
        used_txn_ids=calculation.used_txn_ids,
        document_candidate_ids=["candidate-002", "candidate-003"],
        issues=[],
    )

    assert validate_full_context_verification(verification, calculation) == []


def test_verifier_may_confirm_primary_sources_of_python_rebuilt_calculation() -> None:
    calculation = _calculation().model_copy(deep=True)
    calculation.reasoning_summary = "Python rebuilt the full trigger from scoped ledger data."
    verification = FullContextVerification(
        accepted=True,
        actual=calculation.actual,
        status=calculation.status,
        used_txn_ids=["TXN-X51-001"],
        document_candidate_ids=[],
        issues=[],
    )

    assert validate_full_context_verification(verification, calculation) == []


def test_full_context_uses_independent_calculator_and_verifier_without_real_api(
    monkeypatch,
) -> None:
    calculation = _calculation()
    verification = FullContextVerification(
        accepted=True,
        actual=calculation.actual,
        status=calculation.status,
        used_txn_ids=calculation.used_txn_ids,
        document_candidate_ids=[],
        issues=[],
    )

    class FakeStructured:
        def __init__(self, response) -> None:
            self.response = response
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return self.response

    calculator = FakeStructured(calculation)
    verifier = FakeStructured(verification)

    class FakeLLM:
        def with_structured_output(self, schema):
            return calculator if schema is FullContextCalculation else verifier

    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: FakeLLM())

    result = resolve_full_context([_request()])["X51/6.1"]

    assert result.accepted is True
    assert result.calculation == calculation
    assert result.verification == verification
    assert calculator.calls == 1
    assert verifier.calls == 1


def test_calculator_and_verifier_disagreement_repeats_whole_pair_once(monkeypatch) -> None:
    calculation = _calculation()

    class Calculator:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return calculation

    class Verifier:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            actual = Decimal("0.40") if self.calls == 1 else calculation.actual
            return FullContextVerification(
                accepted=True,
                actual=actual,
                status=calculation.status,
                used_txn_ids=calculation.used_txn_ids,
                document_candidate_ids=[],
                issues=[],
            )

    calculator = Calculator()
    verifier = Verifier()

    class FakeLLM:
        def with_structured_output(self, schema):
            return calculator if schema is FullContextCalculation else verifier

    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: FakeLLM())

    result = resolve_full_context([_request()])["X51/6.1"]

    assert result.accepted is True
    assert result.rounds == 2
    assert calculator.calls == 2
    assert verifier.calls == 2


def test_second_calculator_round_receives_verifier_feedback(monkeypatch) -> None:
    calculation = _calculation()

    class Calculator:
        def __init__(self) -> None:
            self.messages = []

        async def ainvoke(self, messages):
            self.messages.append(messages)
            return calculation

    class Verifier:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return FullContextVerification(
                accepted=self.calls == 2,
                actual=calculation.actual,
                status=calculation.status,
                used_txn_ids=calculation.used_txn_ids,
                document_candidate_ids=[],
                issues=[] if self.calls == 2 else ["include the conditional obligation"],
            )

    calculator = Calculator()
    verifier = Verifier()

    class FakeLLM:
        def with_structured_output(self, schema):
            return calculator if schema is FullContextCalculation else verifier

    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: FakeLLM())

    result = resolve_full_context([_request()])["X51/6.1"]

    assert result.accepted is True
    assert "include the conditional obligation" in calculator.messages[1][-1]["content"]


def test_accepted_full_context_calculation_becomes_auditable_answer() -> None:
    request = _request()
    trace = EvaluationTrace()

    answer = evaluate(
        request.rule,
        list(request.ledger),
        full_context_calculation=_calculation(),
        trace=trace,
    )

    assert answer.actual == Decimal("0.4175")
    assert answer.status == "BREACH"
    assert answer.basis == ["TXN-X51-001", "TXN-X51-004"]
    assert answer.note == "llm_full_context_verified"
    assert trace.branch == "llm_full_context_verified"


def test_unsupported_capability_uses_verified_full_context_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    (root / "submission_template.json").write_text(
        json.dumps({"answers": {"X51": {"6.1": {}}}}),
        encoding="utf-8",
    )
    (root / "master_ledger_2025.csv").write_text(
        "txn_id,date,account_id,counterparty,description,amount,currency\n"
        "TXN-X51-001,2025-06-01,ACC-5100,Agency,marketing campaign,-835000,USD\n"
        "TXN-X51-004,2025-06-01,ACC-5100,Customer,service revenue,2000000,USD\n",
        encoding="utf-8",
    )
    agreement = Document(
        path=Path("agreement.pdf"),
        text=(
            "ДОГОВОР БАНКОВСКОГО ЗАЙМА ACC-5100\n"
            "Статья 6 Финансовые ковенанты\n"
            "Пункт 6.1 Complex promotion intensity ratio.\n"
            "За период с 2025-01-01 по 2025-12-31 marketing expenses / revenue "
            "must not exceed 0.40x.\nСтатья 7"
        ),
        kind=DocKind.CREDIT_AGREEMENT,
        edition=Edition.CURRENT,
        account_ids=["ACC-5100"],
        pages=1,
    )
    formula = FormulaSpec(
        output_kind=OutputKind.RATIO,
        numerator_agg=AggKind.SUM_OUTFLOW,
        numerator_categories=["marketing"],
        denominator_agg=AggKind.REVENUE,
        comparator="<=",
    )

    monkeypatch.setattr("halyk.run.extract_formulas", lambda rules: {"X51/6.1": formula})

    def unsupported(requests):
        return {
            request.key: CapabilityResult(
                GenericFormulaSpec(
                    mode=CovenantMode.UNSUPPORTED,
                    supported=False,
                    reason="Formula languages cannot represent the covenant exactly",
                    clause_evidence=request.rule.text,
                ),
                attempts=1,
            )
            for request in requests
        }

    monkeypatch.setattr("halyk.run.resolve_capabilities", unsupported)

    calculation = _calculation()
    verification = FullContextVerification(
        accepted=True,
        actual=calculation.actual,
        status=calculation.status,
        used_txn_ids=calculation.used_txn_ids,
        document_candidate_ids=[],
        issues=[],
    )

    def full_context(requests):
        assert [request.key for request in requests] == ["X51/6.1"]
        return {
            "X51/6.1": FullContextResult(
                calculation,
                verification,
                accepted=True,
                rounds=1,
            )
        }

    monkeypatch.setattr("halyk.run.resolve_full_context", full_context)
    writer = TraceWriter.create(tmp_path / "trace")

    report = solve(data_dir=root, documents=[agreement], use_llm=True, trace=writer)

    answer = report.answers["X51"]["6.1"]
    assert answer.actual == Decimal("0.4175")
    assert answer.status == "BREACH"
    assert answer.note == "llm_full_context_verified"
    traced = json.loads((writer.root / "11_formulas/full_context.json").read_text(encoding="utf-8"))
    assert traced["X51/6.1"]["reason"] == "capability_unsupported"
    readiness = json.loads((writer.root / "private_readiness.json").read_text())
    assert readiness["checks"]["full_context_accepted"] == 1
    assert "unsupported_formula" not in {finding["code"] for finding in readiness["findings"]}


def test_deterministic_rule_is_never_eligible_for_full_context() -> None:
    rule = _request().rule
    rule.kind = RuleKind.MIN_REVENUE

    assert (
        _full_context_reason(
            "X51/6.1",
            rule=rule,
            formulas={},
            generic_formulas={},
            capability_results={},
            generic_verifications={},
            external_metrics={},
        )
        is None
    )


def test_missing_required_document_metric_uses_full_context_instead_of_zero() -> None:
    rule = _request().rule
    generic = GenericFormulaSpec(
        mode=CovenantMode.GENERIC_NUMERIC,
        supported=True,
        reason="Debt divided by EBITDA",
        clause_evidence=rule.text,
        expression={"op": "metric", "metric": "total_debt"},
        comparator="<=",
        required_metrics=[
            {
                "name": "total_debt",
                "source": "document",
                "description": "Total debt",
                "evidence_terms": ["Total debt"],
            }
        ],
    )

    assert (
        _full_context_reason(
            "X51/6.1",
            rule=rule,
            formulas={},
            generic_formulas={"X51/6.1": generic},
            capability_results={},
            generic_verifications={},
            external_metrics={},
        )
        == "missing_document_metrics"
    )
