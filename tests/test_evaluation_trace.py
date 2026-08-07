from __future__ import annotations

from datetime import date
from decimal import Decimal

from halyk.categorize import Category
from halyk.evaluate import EvaluationTrace, evaluate, find_evidence
from halyk.ledger import LedgerEntry
from halyk.rules import Rule, RuleKind


def _entry(txn_id: str, day: date, amount: str) -> LedgerEntry:
    return LedgerEntry(
        txn_id=txn_id,
        scenario_id="P1",
        day=day,
        account_id="ACC-0001",
        counterparty="Vendor",
        description="equipment",
        amount=Decimal(amount),
        currency="USD",
        category=Category.CAPEX,
    )


def test_evaluation_trace_explains_scope_aggregate_and_evidence_trials() -> None:
    rule = Rule(
        scenario_id="P1",
        clause="6.1",
        heading="Maximum capex",
        text="Capex must not exceed $50",
        kind=RuleKind.MAX_CATEGORY_SPEND,
        comparator="<=",
        threshold=Decimal("50"),
        period=(date(2025, 1, 1), date(2025, 3, 31)),
        categories=frozenset({Category.CAPEX}),
    )
    entries = [
        _entry("TXN-P1-1", date(2025, 2, 1), "-60"),
        _entry("TXN-P1-2", date(2025, 4, 1), "-100"),
    ]
    entries[0].audit_reclassified = True
    details = EvaluationTrace()

    answer = evaluate(rule, entries, trace=details)
    trials: dict[str, str] = {}
    evidence = find_evidence(rule, entries, answer, trials=trials)

    assert answer.status == "BREACH"
    assert details.scope_txn_ids == ["TXN-P1-1"]
    assert details.branch == "max_category_spend"
    assert details.aggregates == {"selected_total": Decimal("60")}
    assert details.basis_txn_ids == ["TXN-P1-1"]
    assert details.actual == Decimal("60")
    assert details.threshold == Decimal("50")
    assert details.comparator == "<="
    assert details.status == "BREACH"
    assert evidence == "TXN-P1-1"
    assert trials == {"TXN-P1-1": "COMPLIANT"}


def test_ordinary_aggregate_line_is_not_evidence() -> None:
    rule = Rule(
        scenario_id="P1",
        clause="6.1",
        heading="Maximum capex",
        text="Capex must not exceed $50",
        kind=RuleKind.MAX_CATEGORY_SPEND,
        comparator="<=",
        threshold=Decimal("50"),
        period=None,
        categories=frozenset({Category.CAPEX}),
    )
    entries = [_entry("TXN-P1-1", date(2025, 2, 1), "-60")]

    answer = evaluate(rule, entries)

    assert find_evidence(rule, entries, answer) is None
