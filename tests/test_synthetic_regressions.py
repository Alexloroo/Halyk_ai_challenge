from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from halyk.audit import (
    AdjustmentKind,
    apply_adjustments,
    apply_fx_settlements,
    extract_adjustments,
    extract_fx_settlements,
    is_actionable_audit_text,
)
from halyk.categorize import Category, assess_category, categorize
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
        scenario_id="X",
        clause="6.1",
        heading="test",
        text=text,
        kind=kind,
        comparator="<=",
        threshold=Decimal(threshold),
        period=None,
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


def test_private_debt_and_transfer_transactions_get_semantic_categories() -> None:
    assert categorize("Promissory note proceeds", is_inflow=True) is Category.FINANCING
    assert categorize("Term loan principal repayment 2025") is Category.DEBT_PRINCIPAL
    assert categorize("Processing equipment transfer to group entity") is Category.ASSET_TRANSFER
    assert categorize("Intercompany distribution settlement") is Category.DISTRIBUTION


def test_category_assessment_resolves_known_conflicts_and_routes_novel_text_to_llm() -> None:
    financing = assess_category("Term loan facility drawdown", is_inflow=True)
    marketing = assess_category("Point-of-sale marketing materials", is_inflow=False)
    novel = assess_category("Orbital fleet synchronization", is_inflow=False)

    assert financing.category is Category.FINANCING
    assert financing.needs_llm is False
    assert marketing.category is Category.MARKETING
    assert marketing.needs_llm is False
    assert novel.category is Category.OPEX
    assert novel.needs_llm is True


def test_related_party_matching_is_exact_after_safe_name_normalization() -> None:
    parties = RelatedParties(
        scenario_id="X",
        threshold_percent=Decimal("25"),
        holdings=[],
        names=frozenset({"Nova Holdings LLP"}),
        unrestricted=frozenset(),
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
        DocKind.CREDIT_AGREEMENT,
        Edition.CURRENT,
        ["ACC-9999"],
        5,
    )
    executed = Document(
        Path("agreement.pdf"),
        "ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР ДОГОВОР БАНКОВСКОГО ЗАЙМА",
        DocKind.CREDIT_AGREEMENT,
        Edition.CURRENT,
        ["ACC-9999"],
        2,
    )

    assert pick([training, executed], DocKind.CREDIT_AGREEMENT, "ACC-9999") is executed


def test_subaccount_identifier_does_not_match_primary_account() -> None:
    assert ACCOUNT.findall("KYC-ACC Account ID: ACC-8819-02") == []
    assert ACCOUNT.findall("KYC-ACC Account ID: ACC-8819") == ["ACC-8819"]
    assert ACCOUNT.findall("KYC-ACC Account ID: ACC-123456") == ["ACC-123456"]


def test_non_acc_account_identifier_is_linked_to_its_documents() -> None:
    assert ACCOUNT.findall("Account TELE-4471") == ["TELE-4471"]


def test_rule_parser_accepts_private_like_formatting_and_currency() -> None:
    agreement = """
Clause 6 . 1) Minimum revenue.
Revenue from 01.01.2026 to 31.12.2026 must be at least 500 000,00 USD.
Clause 6.2: Professional services ratio.
Professional services / revenue must not exceed 0,30x.
Article 7
"""
    rules = extract_rules("PRIVATE99", agreement)

    assert rules["6.1"].threshold == Decimal("500000.00")
    assert rules["6.1"].period == (date(2026, 1, 1), date(2026, 12, 31))
    assert rules["6.1"].comparator == ">="
    assert rules["6.2"].threshold == Decimal("0.30")
    assert rules["6.2"].comparator == "<="


def test_rule_parser_supports_worded_percent_and_nearest_comparator() -> None:
    agreement = """
Пункт 6.1 Условный лимит.
Если выручка окажется менее $4,000,000.00, капитальные затраты не должны превышать
80 процентов совокупной выручки.
Пункт 6.2 Минимальная квартальная выручка.
Заёмщик обязуется не допускать снижения выручки ниже $3,000,000.00.
Статья 7
"""

    rules = extract_rules("PRIVATE", agreement)

    assert rules["6.1"].threshold == Decimal("0.8")
    assert rules["6.1"].comparator == "<="
    assert rules["6.2"].threshold == Decimal("3000000")
    assert rules["6.2"].comparator == ">="


def test_conditional_rule_uses_obligation_threshold_not_trigger_threshold() -> None:
    agreement = """
Пункт 6.1 Условный лимит капитальных затрат.
Если Коэффициент долговой нагрузки превышает 3.00x, то Заёмщик обязуется не
допускать превышения капитальными затратами величины $2,500,000.00.
Пункт 6.2 Insurance Cover Linked to Capital Expenditure.
Если капитальные затраты превышают $1,500,000.00, Заёмщик обязуется поддерживать
страховые премии в размере не менее $250,000.00.
Пункт 6.3 Conditional capital intensity.
If quarterly revenue is below $4,000,000.00, the Borrower must ensure that capex to
revenue does not exceed 0.32x.
Статья 7
"""

    rules = extract_rules("PRIVATE", agreement)

    assert rules["6.1"].threshold == Decimal("2500000.00")
    assert rules["6.1"].comparator == "<="
    assert rules["6.2"].threshold == Decimal("250000.00")
    assert rules["6.2"].comparator == ">="
    assert rules["6.3"].threshold == Decimal("0.32")
    assert rules["6.3"].comparator == "<="


def test_only_actionable_unknown_documents_are_audit_candidates() -> None:
    assert is_actionable_audit_text("ACC-123456 weekly operational status") is False
    assert (
        is_actionable_audit_text("Операция TXN-Z9-0001 исключена из ковенантного периода.") is True
    )


def test_cutoff_uses_document_financial_year_instead_of_hardcoded_year() -> None:
    text = """
Операция TXN-Z9-0001 относится к услугам, оказанным в период с 2031-01-01.
ZETA JSC · 2030 ФИН. ГОД
"""

    adjustments = extract_adjustments(text)

    assert [(item.kind, item.txn_id) for item in adjustments] == [
        (AdjustmentKind.EXCLUDE, "TXN-Z9-0001")
    ]


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


def test_exact_audited_fx_settlement_overrides_ambiguous_ledger_amount() -> None:
    ledger_entry = entry("TXN-J5-0002", "-668678.14", Category.CAPEX)
    ledger_entry.counterparty = "Nordwerk Aufbereitungstechnik GmbH"
    ledger_entry.currency = "EUR"
    settlements = extract_fx_settlements(
        "Расчёты с контрагентом «Nordwerk Aufbereitungstechnik GmbH»: счёт на сумму "
        "81,627.50 EUR урегулирован платежом в долларах США в размере $93,055.35."
    )

    assert apply_fx_settlements([ledger_entry], settlements) == {"TXN-J5-0002"}
    assert ledger_entry.amount == Decimal("-93055.35")
    assert ledger_entry.currency == "USD"
    assert ledger_entry.audit_corrected is True


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
        entry("TXN-X-2", "1000", Category.FINANCING, "Loan facility proceeds"),
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
    answer = evaluate(
        rule(text="marketing only if financing exceeds $1,500"),
        entries,
        formula=formula,
        trace=details,
    )

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
        "X/6.1": FormulaSpec(
            output_kind=OutputKind.RATIO,
            numerator_agg=AggKind.SUM_OUTFLOW,
            numerator_categories=["capex"],
            denominator_agg=AggKind.EBITDA,
            comparator="<=",
        ),
        "X/6.2": FormulaSpec(
            output_kind=OutputKind.RATIO,
            numerator_agg=AggKind.SUM_OUTFLOW,
            numerator_categories=["capex"],
            denominator_agg=AggKind.EBITDA,
            comparator="<=",
        ),
    }
    apply_formula_context(rules, formulas)

    expected = {"opex", "utilities", "marketing", "professional", "personnel"}
    assert set(formulas["X/6.1"].denominator_categories) == expected
    assert set(formulas["X/6.2"].denominator_categories) == expected


def test_coordinated_russian_ebitda_definition_is_authoritative() -> None:
    rules = {
        "X": {
            "6.1": rule(
                text=(
                    "EBITDA = выручка за вычетом операционных, коммунальных, "
                    "маркетинговых, профессиональных расходов и расходов на персонал."
                )
            ),
        }
    }
    formulas = {
        "X/6.1": FormulaSpec(
            output_kind=OutputKind.RATIO,
            numerator_agg=AggKind.SUM_OUTFLOW,
            numerator_categories=["capex"],
            denominator_agg=AggKind.EBITDA,
            denominator_categories=["marketing", "personnel"],
            comparator="<=",
        ),
    }

    apply_formula_context(rules, formulas)

    assert set(formulas["X/6.1"].denominator_categories) == {
        "opex",
        "utilities",
        "marketing",
        "professional",
        "personnel",
    }


def test_model_ebitda_categories_remain_when_no_explicit_definition_exists() -> None:
    rules = {"X": {"6.1": rule(text="capex / EBITDA <= 1x")}}
    formulas = {
        "X/6.1": FormulaSpec(
            output_kind=OutputKind.RATIO,
            numerator_agg=AggKind.SUM_OUTFLOW,
            numerator_categories=["capex"],
            denominator_agg=AggKind.EBITDA,
            denominator_categories=["opex", "personnel"],
            comparator="<=",
        ),
    }

    apply_formula_context(rules, formulas)

    assert formulas["X/6.1"].denominator_categories == ["opex", "personnel"]


def test_single_operation_formula_uses_largest_transaction_not_category_total() -> None:
    spec = FormulaSpec(
        output_kind=OutputKind.DOLLAR_AMOUNT,
        numerator_agg=AggKind.MAX_SINGLE_TRANSACTION,
        numerator_categories=["capex"],
        comparator="<=",
    )
    covenant = rule(text="каждая отдельная операция не выше $500")
    answer = evaluate(
        covenant,
        [
            entry("TXN-X-1", "-400", Category.CAPEX),
            entry("TXN-X-2", "-300", Category.CAPEX),
        ],
        formula=spec,
    )

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
    answer = evaluate(
        covenant,
        [
            entry("TXN-X-1", "-40007", Category.CAPEX),
            entry("TXN-X-2", "1000000", Category.REVENUE),
        ],
        formula=spec,
    )
    assert answer.rounded() == 0.04
    assert answer.status == "BREACH"
