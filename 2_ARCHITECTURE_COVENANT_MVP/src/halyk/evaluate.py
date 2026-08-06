"""Rule + ledger entries -> actual, status, evidence.

No model runs here. Given the same rules and the same entries this produces the
same answer, every time.

Two things from the case shape the code more than anything else:

  * `actual` is always positive and is always reported, including when the
    covenant is COMPLIANT and even when it sits above its own limit;
  * a missing cell scores what a wrong one scores, so nothing here is allowed
    to refuse — an unrecognised rule still yields a best-effort answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .categorize import OPEX_LIKE, Category
from .ledger import LedgerEntry
from .rules import Rule, RuleKind

CENTS = Decimal("0.01")


@dataclass
class Answer:
    scenario_id: str
    clause: str
    status: str                       # COMPLIANT | BREACH
    actual: Decimal
    evidence_txn_id: str | None = None
    basis: list[str] = field(default_factory=list)   # txn ids behind the number
    note: str = ""

    def rounded(self) -> float:
        return float(self.actual.quantize(CENTS, rounding=ROUND_HALF_UP))


def in_period(entry: LedgerEntry, rule: Rule) -> bool:
    if rule.period is None:
        return True
    start, end = rule.period
    return start <= entry.day <= end


def _select(entries: list[LedgerEntry], rule: Rule) -> list[LedgerEntry]:
    return [e for e in entries if e.usable and in_period(e, rule)]


def _sum(entries: list[LedgerEntry]) -> Decimal:
    return sum((e.magnitude for e in entries), Decimal(0))


def _spend(entries: list[LedgerEntry], categories: frozenset[Category]) -> list[LedgerEntry]:
    """Outflows in the given categories. Contra entries never count as spend."""
    wanted = categories or OPEX_LIKE
    return [
        e for e in entries
        if e.is_outflow and e.category in wanted and e.category is not Category.CONTRA
    ]


def _revenue(entries: list[LedgerEntry]) -> list[LedgerEntry]:
    """Genuine trading income only — refunds and credits are not revenue."""
    return [e for e in entries if e.is_inflow and e.category is Category.REVENUE]


def _related(entries: list[LedgerEntry]) -> list[LedgerEntry]:
    return [e for e in entries if e.is_outflow and e.is_related_party]


def _verdict(actual: Decimal, rule: Rule) -> str:
    if rule.threshold is None:
        return "COMPLIANT"
    if rule.is_minimum:
        return "COMPLIANT" if actual >= rule.threshold else "BREACH"
    return "COMPLIANT" if actual <= rule.threshold else "BREACH"


def evaluate(rule: Rule, entries: list[LedgerEntry]) -> Answer:
    scope = _select(entries, rule)

    if rule.kind is RuleKind.MIN_REVENUE:
        chosen = _revenue(scope)
        actual = _sum(chosen)

    elif rule.kind is RuleKind.MAX_CATEGORY_SPEND:
        chosen = _spend(scope, rule.categories)
        actual = _sum(chosen)

    elif rule.kind is RuleKind.MAX_RELATED_PARTY:
        chosen = _related(scope)
        actual = _sum(chosen)

    elif rule.kind is RuleKind.RELATED_PARTY_SHARE:
        chosen = _related(scope)
        revenue = _sum(_revenue(scope))
        actual = (_sum(chosen) / revenue) if revenue else Decimal(0)

    elif rule.kind is RuleKind.RATIO:
        # Ratio of the first named category over the rest, which covers capital
        # intensity, coverage and leverage tests alike.
        ordered = sorted(rule.categories, key=lambda c: c.value)
        if len(ordered) >= 2:
            numerator = _sum(_spend(scope, frozenset({ordered[0]})))
            denominator = _sum(_spend(scope, frozenset(ordered[1:])))
        else:
            numerator = _sum(_spend(scope, rule.categories))
            denominator = _sum(_revenue(scope))
        chosen = _spend(scope, rule.categories)
        actual = (numerator / denominator) if denominator else Decimal(0)

    else:
        chosen = _spend(scope, rule.categories)
        actual = _sum(chosen)

    return Answer(
        scenario_id=rule.scenario_id,
        clause=rule.clause,
        status=_verdict(actual, rule),
        actual=abs(actual),
        basis=[e.txn_id for e in chosen],
        note=rule.kind.value,
    )


def find_evidence(rule: Rule, entries: list[LedgerEntry], answer: Answer) -> str | None:
    """The transaction whose removal flips the verdict.

    Straight from the case: evidence is the line that *decides* the outcome, not
    the largest contributor and not the one that happened to tip a running total.
    Removing it changes the verdict; if no single line does, there is none.
    """
    if answer.status != "BREACH" or not answer.basis:
        return None

    deciding = [
        txn_id
        for txn_id in answer.basis
        if evaluate(rule, [e for e in entries if e.txn_id != txn_id]).status != "BREACH"
    ]
    return deciding[0] if len(deciding) == 1 else None
