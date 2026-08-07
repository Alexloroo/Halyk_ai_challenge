from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from halyk.audit import AdjustmentKind, apply_adjustments, extract_adjustments
from halyk.categorize import Category, categorize
from halyk.docs import ACCOUNT, DocKind, Document, Edition, pick
from halyk.evaluate import EvaluationTrace, evaluate, find_evidence
from halyk.ledger import LedgerEntry
from halyk.llm_extract import AggKind, FormulaSpec, OutputKind, apply_formula_context
from halyk.parties import RelatedParties, mark_related
from halyk.rules import Rule, RuleKind, extract_rules


def entry(txn_id: str, amount: str, category: Category, description: str = "test") -> LedgerEntry:
    return LedgerEntry(
        txn_id=txn_id,
        scenario_id="X",
        day=date(2025, 6, 1),
        account_id="ACC-9999",
        counterparty="Vendor LLP",
        description=description,
        amount=Decimal(amount),
        currency="USD",
        category=category,
    )


def rule(*, text: str, threshold: str = "500", kind: RuleKind = RuleKind.RATIO) -> Rule:
    return Rule(
        scenario_id="X", clause="6.1", heading="test", text=text, kind=kind,
        comparator="<=", threshold=Decimal(threshold), period=None,
    )


def test_rule_categories_use_heading_and_english_aliases_and_cyrillic_ratio() -> None:
    agreement = """
Пункт 6.1 Максимальные капитальные затраты.
Capex за период не выше $500,000.00.
Пункт 6.2 Professional services ratio.
Professional / revenue не выше 0.30х.
Пункт 6.3 Personnel costs.
Personnel spend не выше $900,000.00.
Статья 7
"""
    rules = extract_rules("X", agreement)

    assert rules["6.1"].categories == frozenset({Category.CAPEX})
    assert rules["6.2"].categories == frozenset({Category.PROFESSIONAL, Category.REVENUE})
    assert rules["6.2"].threshold == Decimal("0.30")
    assert rules["6.3"].categories == frozenset({Category.PERSONNEL})


def test_russian_ebitda_definition_recognizes_professional_expenses() -> None:
    agreement = """
Пункт 6.1 Коэффициент к EBITDA.
EBITDA = выручка минус операционные расходы, коммунальные, маркетинговые,
профессиональные и расходы на персонал; коэффициент не выше 0.30x.
Статья 7
"""
    assert Category.PROFESSIONAL in extract_rules("X", agreement)["6.1"].categories


def test_category_classifier_handles_control_system_and_shared_service_payment() -> None:
    assert categorize("Purchase of furnace control system") is Category.CAPEX
    assert categorize("Shared services payment") is Category.PROFESSIONAL
    assert categorize("Group warehouse service") is Category.OPEX


def test_related_party_matching_is_exact_after_safe_name_normalization() -> None:
    parties = RelatedParties(
        scenario_id="X", threshold_percent=Decimal("25"), holdings=[],
        names=frozenset({"Nova Holdings LLP"}), unrestricted=frozenset(),
    )
    exact = entry("TXN-X-1", "-1", Category.OPEX)
    exact.counterparty = "Nova Holdings LLP (Almaty office)"
    advisory = entry("TXN-X-2", "-1", Category.PROFESSIONAL)
    advisory.counterparty = "Nova Holdings Advisory LLC"

    assert mark_related([exact, advisory], parties) == 1
    assert exact.is_related_party is True
    assert advisory.is_related_party is False


def test_document_picker_prefers_executed_agreement_over_long_training_memo() -> None:
    training = Document(
        Path("training.pdf"),
        "МЕТОДИЧЕСКИЙ МЕМОРАНДУМ. Документ не является кредитным договором "
        "и не создаёт обязательств. " * 100,
        DocKind.CREDIT_AGREEMENT, Edition.CURRENT, ["ACC-9999"], 5,
    )
    executed = Document(
        Path("agreement.pdf"), "ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР ДОГОВОР БАНКОВСКОГО ЗАЙМА",
        DocKind.CREDIT_AGREEMENT, Edition.CURRENT, ["ACC-9999"], 2,
    )

    assert pick([training, executed], DocKind.CREDIT_AGREEMENT, "ACC-9999") is executed


def test_subaccount_identifier_does_not_match_primary_account() -> None:
    assert ACCOUNT.findall("KYC-ACC Account ID: ACC-8819-02") == []
    assert ACCOUNT.findall("KYC-ACC Account ID: ACC-8819") == ["ACC-8819"]


def test_audit_adjustments_do_not_cross_transaction_blocks() -> None:
    text = """
Операция TXN-X-0006 первоначально учтенная как маркетинговые расходы,
переклассифицирована аудитором как капитальные затраты.
Операция TXN-X-0007 исключена из ковенантного периода.
"""
    adjustments = extract_adjustments(text)

    assert [(a.kind, a.txn_id) for a in adjustments] == [
        (AdjustmentKind.RECLASSIFY, "TXN-X-0006"),
        (AdjustmentKind.EXCLUDE, "TXN-X-0007"),
    ]


def test_audit_document_without_operation_blocks_is_valid() -> None:
    assert extract_adjustments("Переклассификаций за ковенантный период не требовалось.") == []


def test_audit_missing_amount_has_typed_lineage_and_can_be_evidence() -> None:
    missing = entry("TXN-X-0001", "-1", Category.CAPEX)
    missing.amount = None
    adjusted = apply_adjustments(
        [missing],
        extract_adjustments(
            "Операция TXN-X-0001 сумма не отражена в выгрузке; "
            "фактическая сумма составляет $600.00."
        ),
    )[0]
    covenant = rule(text="Capex must not exceed $500", kind=RuleKind.MAX_CATEGORY_SPEND)
    covenant.categories = frozenset({Category.CAPEX})
    answer = evaluate(covenant, [adjusted])

    assert adjusted.audit_corrected is True
    assert find_evidence(covenant, [adjusted], answer) == adjusted.txn_id


def test_conditional_formula_uses_financing_trigger_but_reports_tested_actual() -> None:
    entries = [
        entry("TXN-X-1", "-650", Category.MARKETING),
        entry("TXN-X-2", "1000", Category.UNKNOWN, "Loan facility proceeds"),
    ]
    formula = FormulaSpec(
        output_kind=OutputKind.DOLLAR_AMOUNT,
        numerator_agg=AggKind.SUM_OUTFLOW,
        numerator_categories=["marketing"],
        comparator="<=",
        is_conditional=True,
        condition_threshold_dollars=1500,
        condition_agg=AggKind.FINANCING_INFLOW,
    )
    details = EvaluationTrace()
    answer = evaluate(rule(text="marketing only if financing exceeds $1,500"), entries,
                      formula=formula, trace=details)

    assert answer.actual == Decimal("650")
    assert answer.status == "COMPLIANT"
    assert details.aggregates["condition_value"] == Decimal("1000")


def test_explicit_ebitda_definition_is_propagated_to_all_scenario_formulas() -> None:
    rules = {
        "X": {
            "6.1": rule(
                text="EBITDA = revenue less opex, utilities, marketing, professional and personnel"
            ),
            "6.2": rule(text="capex / EBITDA <= 1x"),
        }
    }
    formulas = {
        "X/6.1": FormulaSpec(output_kind=OutputKind.RATIO, numerator_agg=AggKind.SUM_OUTFLOW,
                              numerator_categories=["capex"], denominator_agg=AggKind.EBITDA,
                              comparator="<="),
        "X/6.2": FormulaSpec(output_kind=OutputKind.RATIO, numerator_agg=AggKind.SUM_OUTFLOW,
                              numerator_categories=["capex"], denominator_agg=AggKind.EBITDA,
                              comparator="<="),
    }
    apply_formula_context(rules, formulas)

    expected = {"opex", "utilities", "marketing", "professional", "personnel"}
    assert set(formulas["X/6.1"].denominator_categories) == expected
    assert set(formulas["X/6.2"].denominator_categories) == expected


def test_single_operation_formula_uses_largest_transaction_not_category_total() -> None:
    spec = FormulaSpec(
        output_kind=OutputKind.DOLLAR_AMOUNT,
        numerator_agg=AggKind.MAX_SINGLE_TRANSACTION,
        numerator_categories=["capex"], comparator="<=",
    )
    covenant = rule(text="каждая отдельная операция не выше $500")
    answer = evaluate(covenant, [
        entry("TXN-X-1", "-400", Category.CAPEX),
        entry("TXN-X-2", "-300", Category.CAPEX),
    ], formula=spec)

    assert answer.actual == Decimal("400")


def test_ratio_verdict_uses_unrounded_value() -> None:
    covenant = rule(text="ratio <= 0.04x", threshold="0.04")
    spec = FormulaSpec(
        output_kind=OutputKind.RATIO,
        numerator_agg=AggKind.SUM_OUTFLOW,
        numerator_categories=["capex"],
        denominator_agg=AggKind.REVENUE,
        comparator="<=",
    )
    answer = evaluate(covenant, [
        entry("TXN-X-1", "-40007", Category.CAPEX),
        entry("TXN-X-2", "1000000", Category.REVENUE),
    ], formula=spec)
    assert answer.rounded() == 0.04
    assert answer.status == "BREACH"
