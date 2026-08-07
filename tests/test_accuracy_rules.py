from __future__ import annotations

from datetime import date
from decimal import Decimal

from halyk.audit import extract_group_capex
from halyk.categorize import Category
from halyk.evaluate import evaluate
from halyk.ledger import LedgerEntry
from halyk.llm_extract import AggKind, FormulaSpec, OutputKind
from halyk.parties import extract_related_parties, mark_unrestricted
from halyk.rules import Rule, RuleKind


def _entry(
    txn_id: str,
    amount: str,
    category: Category,
    *,
    counterparty: str = "Vendor LLP",
) -> LedgerEntry:
    return LedgerEntry(
        txn_id=txn_id,
        scenario_id="P1",
        day=date(2025, 6, 1),
        account_id="ACC-0001",
        counterparty=counterparty,
        description="test",
        amount=Decimal(amount),
        currency="USD",
        category=category,
    )


def test_related_party_share_uses_opex_base_when_clause_names_opex() -> None:
    rule = Rule(
        scenario_id="P1",
        clause="6.1",
        heading="Related-party share",
        text="Related party payments / operating expenses <= 0.2x",
        kind=RuleKind.RELATED_PARTY_SHARE,
        comparator="<=",
        threshold=Decimal("0.2"),
        period=None,
        categories=frozenset({Category.OPEX}),
    )
    related = _entry("TXN-P1-1", "-10", Category.OPEX)
    related.is_related_party = True
    entries = [
        related,
        _entry("TXN-P1-2", "-90", Category.OPEX),
        _entry("TXN-P1-3", "1000", Category.REVENUE),
    ]

    answer = evaluate(rule, entries)

    assert answer.actual == Decimal("0.1")


def test_unrestricted_transfer_comes_from_pledge_table() -> None:
    kyc = """
Обеспечительное покрытие дочерних организаций
Дочерняя организация
Доля активов в залоге
Secured Assets LLP
80.0%
Outside Perimeter LLP
12.0%
Дочерние организации, у которых доля активов в залоге ниже 50.0%, находятся вне периметра
обеспечения и для целей Договора рассматриваются как неограниченные.
Бенефициарное владение и контроль
Parent Holdings LLP
70.0%
Организации, в которых Группа владеет 25.0% и более голосующих прав, признаются связанными.
"""
    parties = extract_related_parties("P1", kyc)
    entries = [_entry("TXN-P1-1", "-25", Category.CAPEX, counterparty="Outside Perimeter LLP")]

    mark_unrestricted(entries, parties)

    assert parties.unrestricted == frozenset({"Outside Perimeter LLP"})
    assert entries[0].is_unrestricted_transfer is True


def test_group_capex_and_document_numerator_are_used_in_ratio() -> None:
    capex = extract_group_capex(
        """Net book value at the beginning of the year $ 100\n"
        "Net book value at the end of the year $ 130\n"
        "Depreciation charge for the year $ 20"""
    )
    rule = Rule(
        scenario_id="P1",
        clause="6.1",
        heading="Group capex ratio",
        text="Group capex / EBITDA <= 1x",
        kind=RuleKind.RATIO,
        comparator="<=",
        threshold=Decimal("1"),
        period=None,
    )
    formula = FormulaSpec(
        output_kind=OutputKind.RATIO,
        numerator_agg=AggKind.SUM_OUTFLOW,
        numerator_categories=["capex"],
        denominator_agg=AggKind.SUM_OUTFLOW,
        denominator_categories=["opex"],
        comparator="<=",
    )
    entries = [_entry("TXN-P1-1", "-10", Category.OPEX)]

    answer = evaluate(rule, entries, formula=formula, numerator_constant=capex)

    assert capex == Decimal("50")
    assert answer.actual == Decimal("5")
