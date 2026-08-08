from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from halyk.audit import extract_group_capex
from halyk.categorize import Category
from halyk.docs import DocKind, Document, Edition
from halyk.evaluate import evaluate
from halyk.ledger import LedgerEntry
from halyk.llm_documents import EntityLinkResult, EntityLinkSpec
from halyk.llm_extract import AggKind, FormulaSpec, OutputKind
from halyk.parties import extract_related_parties, mark_related, mark_unrestricted
from halyk.rules import Rule, RuleKind
from halyk.run import _resolve_group_capex_values


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


def test_related_parties_support_worded_fraction_and_explicit_affiliate_records() -> None:
    worded = extract_related_parties(
        "P1",
        """
Astyk Trans Export JSC
4.9%
Syrdarya Capital LLP
27.8%
Организации, в которых Группе принадлежит не менее одной пятой голосующих прав,
признаются связанными сторонами для целей Договора.
""",
    )
    explicit = extract_related_parties(
        "P2",
        "Контрагент «Altyn Capital L.L.P.» классифицирован как АФФИЛИРОВАННОЕ ЛИЦО Заёмщика.",
    )
    entry = _entry("TXN-P1-2", "-25", Category.OPEX, counterparty="Altyn Capital LLP")

    assert worded.threshold_percent == Decimal("20")
    assert worded.names == frozenset({"Syrdarya Capital LLP"})
    assert explicit.resolved is True
    assert explicit.names == frozenset({"Altyn Capital L.L.P."})
    assert mark_related([entry], explicit) == 1


def test_related_parties_support_one_quarter_and_explicit_unrestricted_record() -> None:
    parties = extract_related_parties(
        "P1",
        """
Small Vendor LLP
9.8%
Syrdarya Capital LLP
32.1%
Организации, в которых Группе принадлежит не менее одной четверти голосующих прав,
признаются связанными сторонами.
Entry 2. Counterparty "Altai Ore Processing LLP" is a designated UNRESTRICTED SUBSIDIARY
of the Borrower.
""",
    )

    assert parties.threshold_percent == Decimal("25")
    assert parties.names == frozenset({"Syrdarya Capital LLP"})
    assert parties.unrestricted == frozenset({"Altai Ore Processing LLP"})


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


def test_group_capex_falls_back_to_validated_entity_link(monkeypatch) -> None:
    agreement = Document(
        path=Path("agreement.pdf"),
        text="Borrower: Ili Hydro Controls JSC. Group capital expenditure covenant.",
        kind=DocKind.CREDIT_AGREEMENT,
        edition=Edition.CURRENT,
        account_ids=["ACC-0001"],
        pages=1,
    )
    statement = Document(
        path=Path("statement.pdf"),
        text=(
            "Ili Hydro Controls JSC Consolidated Financial Statements\n"
            "Net book value at the beginning of the year $ 100\n"
            "Net book value at the end of the year $ 130\n"
            "Depreciation charge for the year $ 20"
        ),
        kind=DocKind.UNKNOWN,
        edition=Edition.CURRENT,
        account_ids=["ACC-0001"],
        pages=1,
    )
    covenant = Rule(
        scenario_id="X28",
        clause="6.1",
        heading="Group capex ratio",
        text="Group capital expenditure for Ili Hydro Controls JSC / EBITDA <= 1x",
        kind=RuleKind.RATIO,
        comparator="<=",
        threshold=Decimal("1"),
        period=None,
    )

    def fake_resolve(requests):
        request = requests[0]
        return {
            request.key: EntityLinkResult(
                EntityLinkSpec(
                    borrower_name="Ili Hydro Controls JSC",
                    matched_candidate_id="candidate-001",
                    agreement_evidence="Borrower: Ili Hydro Controls JSC",
                    candidate_evidence="Ili Hydro Controls JSC",
                ),
                attempts=1,
            )
        }

    monkeypatch.setattr("halyk.run.resolve_entity_links", fake_resolve)

    values, records = _resolve_group_capex_values(
        {"X28": {"6.1": covenant}},
        {"X28": agreement},
        {"X28": "ACC-0001"},
        [agreement, statement],
        use_llm=True,
    )

    assert values == {"X28/6.1": Decimal("50")}
    assert records["X28/6.1"]["source"] == "llm_entity_link"
