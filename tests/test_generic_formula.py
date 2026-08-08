from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from halyk import run as run_module
from halyk.categorize import Category
from halyk.evaluate import EvaluationTrace, evaluate
from halyk.generic_formula import (
    CovenantMode,
    ExpressionOp,
    ExpressionSpec,
    GenericFormulaSpec,
    MetricRequirement,
    MetricSource,
    evaluate_expression,
    validate_expression,
)
from halyk.ledger import LedgerEntry
from halyk.llm_capabilities import (
    CapabilityRequest,
    DocumentMetricRequest,
    DocumentMetricSpec,
    EvidenceCandidate,
    _resolve_capability_one,
    _validate_capability,
    _validate_metric,
)
from halyk.llm_extract import AggKind, FormulaSpec, OutputKind
from halyk.rules import Rule, RuleKind
from halyk.tracing.writer import jsonable


def _entry(txn_id: str, amount: str, category: Category) -> LedgerEntry:
    return LedgerEntry(
        txn_id=txn_id,
        scenario_id="N1",
        day=date(2025, 6, 1),
        account_id="ACC-9001",
        counterparty="Vendor LLP",
        description="test",
        amount=Decimal(amount),
        currency="USD",
        category=category,
    )


def _rule() -> Rule:
    return Rule(
        scenario_id="N1",
        clause="6.1",
        heading="Novel coverage ratio",
        text="(Revenue less capex and tax) divided by interest must not exceed 6.0x",
        kind=RuleKind.UNKNOWN,
        comparator="<=",
        threshold=Decimal("6"),
        period=None,
    )


def _novel_expression() -> ExpressionSpec:
    return ExpressionSpec(
        op=ExpressionOp.DIVIDE,
        args=[
            ExpressionSpec(
                op=ExpressionOp.SUBTRACT,
                args=[
                    ExpressionSpec(op=ExpressionOp.METRIC, metric="revenue"),
                    ExpressionSpec(
                        op=ExpressionOp.ADD,
                        args=[
                            ExpressionSpec(
                                op=ExpressionOp.SUM_OUTFLOW,
                                categories=[Category.CAPEX],
                            ),
                            ExpressionSpec(
                                op=ExpressionOp.SUM_OUTFLOW,
                                categories=[Category.TAX],
                            ),
                        ],
                    ),
                ],
            ),
            ExpressionSpec(
                op=ExpressionOp.SUM_OUTFLOW,
                categories=[Category.INTEREST],
            ),
        ],
    )


def test_generic_ast_executes_nested_novel_formula_deterministically() -> None:
    entries = [
        _entry("TXN-N1-1", "100", Category.REVENUE),
        _entry("TXN-N1-2", "-20", Category.CAPEX),
        _entry("TXN-N1-3", "-10", Category.TAX),
        _entry("TXN-N1-4", "-10", Category.INTEREST),
    ]
    expression = _novel_expression()

    result = evaluate_expression(expression, entries, {})

    assert validate_expression(expression) == []
    assert result.value == Decimal("7")
    assert {entry.txn_id for entry in result.entries} == {
        "TXN-N1-1",
        "TXN-N1-2",
        "TXN-N1-3",
        "TXN-N1-4",
    }


def test_generic_formula_uses_existing_deterministic_verdict_path() -> None:
    formula = GenericFormulaSpec(
        mode=CovenantMode.GENERIC_NUMERIC,
        supported=True,
        reason="FormulaSpec cannot express nested subtraction",
        clause_evidence="Revenue less capex and tax",
        expression=_novel_expression(),
        comparator="<=",
        required_metrics=[
            MetricRequirement(
                name="revenue",
                source=MetricSource.LEDGER,
                description="operating revenue",
            )
        ],
    )
    trace = EvaluationTrace()

    answer = evaluate(
        _rule(),
        [
            _entry("TXN-N1-1", "100", Category.REVENUE),
            _entry("TXN-N1-2", "-20", Category.CAPEX),
            _entry("TXN-N1-3", "-10", Category.TAX),
            _entry("TXN-N1-4", "-10", Category.INTEREST),
        ],
        generic_formula=formula,
        trace=trace,
    )

    assert answer.actual == Decimal("7")
    assert answer.status == "BREACH"
    assert trace.branch == "generic_numeric"


def test_document_metric_value_and_scale_are_parsed_locally_from_exact_evidence() -> None:
    candidate = EvidenceCandidate(
        candidate_id="candidate-001",
        source="statement.pdf",
        text="Total debt at year end was USD 1.2 million.",
    )
    request = DocumentMetricRequest(
        key="N1/6.1::total_debt",
        metric="total_debt",
        description="year-end total debt",
        evidence_terms=("Total debt",),
        candidates=(candidate,),
    )
    spec = DocumentMetricSpec(
        metric="total_debt",
        matched_candidate_id="candidate-001",
        evidence="Total debt at year end was USD 1.2 million.",
        value_text="1.2",
        scale="million",
    )

    errors, metric = _validate_metric(spec, request)

    assert errors == []
    assert metric is not None
    assert metric.value == Decimal("1200000.0")
    assert metric.source_document == "statement.pdf"


def test_explicit_debt_definition_reuses_ledger_financing_inflow() -> None:
    formula = GenericFormulaSpec(
        mode=CovenantMode.GENERIC_NUMERIC,
        supported=True,
        reason="conditional cap",
        clause_evidence="conditional cap",
        expression=ExpressionSpec(
            op=ExpressionOp.SUM_OUTFLOW,
            categories=[Category.CAPEX],
        ),
        condition=ExpressionSpec(
            op=ExpressionOp.DIVIDE,
            args=[
                ExpressionSpec(op=ExpressionOp.METRIC, metric="total_debt"),
                ExpressionSpec(op=ExpressionOp.METRIC, metric="ebitda"),
            ],
        ),
        condition_threshold=Decimal("2.4"),
        required_metrics=[
            MetricRequirement(
                name="total_debt",
                source=MetricSource.DOCUMENT,
                description="total debt",
                evidence_terms=["total debt"],
            ),
            MetricRequirement(
                name="ebitda",
                source=MetricSource.LEDGER,
                description="EBITDA",
            ),
        ],
    )

    normalized = run_module._normalize_defined_ledger_metrics(
        formula,
        "Total debt for this purpose means Financing proceeds received during the period.",
    )

    assert normalized.condition is not None
    assert normalized.condition.args[0].metric == "financing_inflow"
    assert {(item.name, item.source) for item in normalized.required_metrics} == {
        ("financing_inflow", MetricSource.LEDGER),
        ("ebitda", MetricSource.LEDGER),
    }


def test_absolute_document_metric_rejects_covenant_ratio_threshold() -> None:
    candidate = EvidenceCandidate(
        candidate_id="candidate-001",
        source="agreement.pdf",
        text="Total debt to EBITDA must not exceed 3.00x.",
    )
    request = DocumentMetricRequest(
        key="N1/6.1::total_debt",
        metric="total_debt",
        description="total debt amount",
        evidence_terms=("Total debt",),
        candidates=(candidate,),
    )
    spec = DocumentMetricSpec(
        metric="total_debt",
        matched_candidate_id="candidate-001",
        evidence=candidate.text,
        value_text="3.00x",
        scale="one",
    )

    errors, metric = _validate_metric(spec, request)

    assert metric is None
    assert "absolute financial metric cannot use a ratio or percentage value" in errors


def test_ratio_document_metric_rejects_value_copied_from_covenant_condition() -> None:
    covenant = (
        "If the Leverage Ratio exceeds 3.00x, Capital Expenditure must not exceed $2,500,000."
    )
    candidate = EvidenceCandidate(
        candidate_id="candidate-001",
        source="agreement.pdf",
        text="Section 6.1. " + covenant,
    )
    request = DocumentMetricRequest(
        key="N1/6.1::leverage_ratio",
        metric="leverage_ratio",
        description="measured leverage ratio",
        evidence_terms=("Leverage Ratio",),
        candidates=(candidate,),
        covenant_text=covenant,
    )
    spec = DocumentMetricSpec(
        metric="leverage_ratio",
        matched_candidate_id="candidate-001",
        evidence="Section 6.1. " + covenant,
        value_text="3.00x",
        scale="one",
    )

    errors, metric = _validate_metric(spec, request)

    assert metric is None
    assert "covenant threshold cannot be used as an observed document metric" in errors


def test_documentary_covenant_returns_boolean_actual_without_llm_arithmetic() -> None:
    formula = GenericFormulaSpec(
        mode=CovenantMode.DOCUMENTARY,
        supported=True,
        reason="Documentary insurance obligation",
        clause_evidence="maintain valid insurance",
        documentary_requirement="A valid insurance policy is in force",
        expected_presence=True,
    )

    answer = evaluate(_rule(), [], generic_formula=formula, documentary_fact=False)

    assert answer.actual == Decimal(0)
    assert answer.status == "BREACH"
    assert answer.note == "generic_documentary"


def test_capability_check_accepts_existing_formula_only_with_available_formula() -> None:
    formula = FormulaSpec(
        output_kind=OutputKind.RATIO,
        numerator_agg=AggKind.SUM_OUTFLOW,
        numerator_categories=["capex"],
        denominator_agg=AggKind.REVENUE,
        comparator="<=",
    )
    plan = GenericFormulaSpec(
        mode=CovenantMode.EXISTING_FORMULA,
        supported=True,
        reason="Existing formula is exact",
        clause_evidence="Revenue less capex and tax",
        comparator="<=",
    )

    assert _validate_capability(plan, CapabilityRequest("N1/6.1", _rule(), formula)) == []
    assert "existing_formula selected but no FormulaSpec is available" in _validate_capability(
        plan, CapabilityRequest("N1/6.1", _rule(), None)
    )


def test_capability_check_rejects_unplanned_or_unused_metrics() -> None:
    plan = GenericFormulaSpec(
        mode=CovenantMode.GENERIC_NUMERIC,
        supported=True,
        reason="document metric",
        clause_evidence="Revenue less capex and tax",
        expression=ExpressionSpec(op=ExpressionOp.METRIC, metric="total_debt"),
        comparator="<=",
        required_metrics=[
            MetricRequirement(
                name="equity",
                source=MetricSource.DOCUMENT,
                description="equity",
                evidence_terms=["Total equity"],
            )
        ],
    )

    errors = _validate_capability(plan, CapabilityRequest("N1/6.1", _rule(), None))

    assert "metric requirements missing: ['total_debt']" in errors
    assert "unused metric requirements: ['equity']" in errors


def test_existing_formula_is_only_replaced_by_a_genuinely_new_capability() -> None:
    formula = FormulaSpec(
        output_kind=OutputKind.RATIO,
        numerator_agg=AggKind.SUM_OUTFLOW,
        numerator_categories=["capex"],
        denominator_agg=AggKind.REVENUE,
        comparator="<=",
    )
    equivalent_plan = GenericFormulaSpec(
        mode=CovenantMode.GENERIC_NUMERIC,
        supported=True,
        reason="Equivalent ratio",
        clause_evidence="Revenue less capex and tax",
        expression=ExpressionSpec(
            op=ExpressionOp.DIVIDE,
            args=[
                ExpressionSpec(op=ExpressionOp.SUM_OUTFLOW, categories=[Category.CAPEX]),
                ExpressionSpec(op=ExpressionOp.METRIC, metric="revenue"),
            ],
        ),
        comparator="<=",
        required_metrics=[
            MetricRequirement(
                name="revenue",
                source=MetricSource.LEDGER,
                description="revenue",
            )
        ],
    )
    novel_plan = equivalent_plan.model_copy(update={"expression": _novel_expression()})

    decide = run_module._needs_capability_fallback

    assert decide(formula, equivalent_plan) is False
    assert decide(formula, novel_plan) is True
    assert decide(None, equivalent_plan) is True


def test_capability_retry_preserves_first_invalid_quote(monkeypatch) -> None:
    formula = FormulaSpec(
        output_kind=OutputKind.RATIO,
        numerator_agg=AggKind.SUM_OUTFLOW,
        numerator_categories=["capex"],
        denominator_agg=AggKind.REVENUE,
        comparator="<=",
    )
    request = CapabilityRequest("N1/6.1", _rule(), formula)

    class SequencedStructured:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            evidence = "Invented paraphrase" if self.calls == 1 else "Revenue less capex and tax"
            return GenericFormulaSpec(
                mode=CovenantMode.EXISTING_FORMULA,
                supported=True,
                reason="Existing formula is exact",
                clause_evidence=evidence,
            )

    async def no_wait(delay: float) -> None:
        return None

    monkeypatch.setattr("halyk.llm_capabilities.asyncio.sleep", no_wait)
    result = asyncio.run(
        _resolve_capability_one(SequencedStructured(), request, asyncio.Semaphore(1))
    )

    assert result.attempts == 2
    assert result.attempt_history[0].response["clause_evidence"] == "Invented paraphrase"
    assert result.attempt_history[0].errors == ["clause_evidence is not an exact supplied excerpt"]
    serialized = jsonable(result)
    assert serialized["attempt_history"][0]["response"]["clause_evidence"] == (
        "Invented paraphrase"
    )
