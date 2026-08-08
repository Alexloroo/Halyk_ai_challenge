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
        )
        is None
    )
