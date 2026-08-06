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
from typing import TYPE_CHECKING

from .categorize import OPEX_LIKE, Category
from .ledger import LedgerEntry
from .rules import Rule, RuleKind

if TYPE_CHECKING:
    from .llm_extract import FormulaSpec

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


def _verdict(actual: Decimal, rule: Rule, *, comparator_override: str | None = None) -> str:
    if rule.threshold is None:
        return "COMPLIANT"
    comp = comparator_override or rule.comparator
    if comp == ">=":
        return "COMPLIANT" if actual >= rule.threshold else "BREACH"
    return "COMPLIANT" if actual <= rule.threshold else "BREACH"


EBITDA_OPEX = frozenset({Category.OPEX})


def _ebitda(entries: list[LedgerEntry]) -> Decimal:
    rev = _sum(_revenue(entries))
    opex = _sum(_spend(entries, EBITDA_OPEX))
    return rev - opex


def _agg(
    entries: list[LedgerEntry], agg: str, cats: frozenset[Category],
) -> tuple[Decimal, list[LedgerEntry]]:
    from .llm_extract import AggKind

    if agg == AggKind.REVENUE:
        chosen = _revenue(entries)
        return _sum(chosen), chosen
    if agg == AggKind.EBITDA:
        rev_entries = _revenue(entries)
        opex_entries = _spend(entries, OPEX_LIKE)
        return _ebitda(entries), rev_entries + opex_entries
    if agg == AggKind.SUM_INFLOW:
        chosen = [e for e in entries if e.is_inflow and (not cats or e.category in cats)]
        return _sum(chosen), chosen
    if agg == AggKind.MAX_SINGLE_CATEGORY:
        if not cats:
            cats = OPEX_LIKE
        best_total = Decimal(0)
        best_entries: list[LedgerEntry] = []
        for cat in cats:
            cat_entries = _spend(entries, frozenset({cat}))
            cat_total = _sum(cat_entries)
            if cat_total > best_total:
                best_total = cat_total
                best_entries = cat_entries
        return best_total, best_entries
    if agg == AggKind.REVENUE_MINUS_MAX_CATEGORY:
        rev_entries = _revenue(entries)
        rev = _sum(rev_entries)
        best_total = Decimal(0)
        best_cat_entries: list[LedgerEntry] = []
        for cat in (cats or OPEX_LIKE):
            cat_entries = _spend(entries, frozenset({cat}))
            cat_total = _sum(cat_entries)
            if cat_total > best_total:
                best_total = cat_total
                best_cat_entries = cat_entries
        return rev - best_total, rev_entries + best_cat_entries
    if agg == AggKind.FINANCING_INFLOW:
        chosen = [
            e for e in entries
            if e.is_inflow and e.category not in (
                Category.REVENUE, Category.CONTRA, Category.INTEREST,
                Category.INSURANCE, Category.LEASE, Category.MARKETING,
            )
        ]
        return _sum(chosen), chosen
    if agg == AggKind.REVENUE_PLUS_FINANCING:
        rev = _revenue(entries)
        fin = [
            e for e in entries
            if e.is_inflow and e.category not in (
                Category.REVENUE, Category.CONTRA, Category.INTEREST,
                Category.INSURANCE, Category.LEASE, Category.MARKETING,
            )
        ]
        chosen = rev + fin
        return _sum(chosen), chosen
    if agg == AggKind.RELATED_PARTY_OUTFLOW:
        chosen = _related(entries)
        return _sum(chosen), chosen
    chosen = _spend(entries, cats)
    return _sum(chosen), chosen


def _cats_from_slugs(slugs: list[str]) -> frozenset[Category]:
    mapping = {c.value: c for c in Category}
    return frozenset(mapping[s] for s in slugs if s in mapping)


def _evaluate_with_formula(
    rule: Rule,
    scope: list[LedgerEntry],
    formula: FormulaSpec,
) -> Answer:
    from .llm_extract import OutputKind

    num_cats = _cats_from_slugs(formula.numerator_categories)
    den_cats = _cats_from_slugs(formula.denominator_categories)

    numerator, num_entries = _agg(scope, formula.numerator_agg, num_cats)

    if formula.is_conditional and formula.condition_threshold_dollars is not None:
        cond_val = abs(numerator)
        if cond_val <= Decimal(str(formula.condition_threshold_dollars)):
            return Answer(
                scenario_id=rule.scenario_id,
                clause=rule.clause,
                status="COMPLIANT",
                actual=Decimal(0),
                basis=[e.txn_id for e in num_entries],
                note="conditional_not_triggered",
            )

    if formula.output_kind == OutputKind.DOLLAR_AMOUNT:
        actual = abs(numerator)
        return Answer(
            scenario_id=rule.scenario_id,
            clause=rule.clause,
            status=_verdict(actual, rule, comparator_override=formula.comparator),
            actual=actual,
            basis=[e.txn_id for e in num_entries],
            note=f"llm_dollar:{formula.numerator_agg}",
        )

    denominator, den_entries = _agg(scope, formula.denominator_agg, den_cats)
    actual = (numerator / denominator) if denominator else Decimal(0)
    chosen = list({e.txn_id: e for e in num_entries + den_entries}.values())

    return Answer(
        scenario_id=rule.scenario_id,
        clause=rule.clause,
        status=_verdict(actual, rule, comparator_override=formula.comparator),
        actual=abs(actual),
        basis=[e.txn_id for e in chosen],
        note=f"llm_ratio:{formula.numerator_agg}/{formula.denominator_agg}",
    )


def evaluate(
    rule: Rule,
    entries: list[LedgerEntry],
    *,
    formula: FormulaSpec | None = None,
) -> Answer:
    scope = _select(entries, rule)

    if formula is not None and rule.kind in (RuleKind.RATIO, RuleKind.UNKNOWN):
        return _evaluate_with_formula(rule, scope, formula)

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


def find_evidence(
    rule: Rule,
    entries: list[LedgerEntry],
    answer: Answer,
    *,
    formula: FormulaSpec | None = None,
) -> str | None:
    """The transaction whose removal flips the verdict."""
    if answer.status != "BREACH" or not answer.basis:
        return None

    deciding = [
        txn_id
        for txn_id in answer.basis
        if evaluate(
            rule,
            [e for e in entries if e.txn_id != txn_id],
            formula=formula,
        ).status != "BREACH"
    ]
    return deciding[0] if len(deciding) == 1 else None
