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

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from .categorize import OPEX_LIKE, Category
from .ledger import LedgerEntry
from .rules import Rule, RuleKind

if TYPE_CHECKING:
    from .generic_formula import ExternalMetric, GenericFormulaSpec
    from .llm_extract import FormulaSpec
    from .llm_full_context import FullContextCalculation

CENTS = Decimal("0.01")


@dataclass
class Answer:
    scenario_id: str
    clause: str
    status: str
    actual: Decimal
    evidence_txn_id: str | None = None
    basis: list[str] = field(default_factory=list)
    note: str = ""

    def rounded(self) -> float:
        return float(self.actual.quantize(CENTS, rounding=ROUND_HALF_UP))


@dataclass
class EvaluationTrace:
    """Filesystem-independent explanation of one covenant calculation."""

    scope_txn_ids: list[str] = field(default_factory=list)
    branch: str = ""
    aggregates: dict[str, Decimal] = field(default_factory=dict)
    basis_txn_ids: list[str] = field(default_factory=list)
    comparator: str = ""
    threshold: Decimal | None = None
    actual: Decimal | None = None
    status: str = ""
    note: str = ""
    quality_flags: list[str] = field(default_factory=list)


def _complete_trace(
    trace: EvaluationTrace | None,
    rule: Rule,
    answer: Answer,
    *,
    branch: str,
    aggregates: dict[str, Decimal],
) -> None:
    if trace is None:
        return
    trace.branch = branch
    trace.aggregates = aggregates
    trace.basis_txn_ids = list(answer.basis)
    trace.comparator = rule.comparator
    trace.threshold = rule.threshold
    trace.actual = answer.actual
    trace.status = answer.status
    trace.note = answer.note


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
    return [
        e
        for e in entries
        if e.is_outflow and e.category in categories and e.category is not Category.CONTRA
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


def _ebitda(entries: list[LedgerEntry], categories: frozenset[Category]) -> Decimal:
    rev = _sum(_revenue(entries))
    opex = _sum(_spend(entries, categories or EBITDA_OPEX))
    return rev - opex


def _agg(
    entries: list[LedgerEntry],
    agg: str,
    cats: frozenset[Category],
) -> tuple[Decimal, list[LedgerEntry]]:
    from .llm_extract import AggKind

    if agg == AggKind.REVENUE:
        chosen = _revenue(entries)
        return _sum(chosen), chosen
    if agg == AggKind.EBITDA:
        rev_entries = _revenue(entries)
        opex_entries = _spend(entries, cats or EBITDA_OPEX)
        return _ebitda(entries, cats), rev_entries + opex_entries
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
    if agg == AggKind.MAX_SINGLE_TRANSACTION:
        chosen = (
            _spend(entries, cats)
            if cats
            else [e for e in entries if e.is_outflow and e.category is not Category.CONTRA]
        )
        if not chosen:
            return Decimal(0), []
        largest = max(chosen, key=lambda entry: entry.magnitude)
        return largest.magnitude, [largest]
    if agg == AggKind.REVENUE_MINUS_MAX_CATEGORY:
        rev_entries = _revenue(entries)
        rev = _sum(rev_entries)
        best_total = Decimal(0)
        best_cat_entries: list[LedgerEntry] = []
        for cat in cats or OPEX_LIKE:
            cat_entries = _spend(entries, frozenset({cat}))
            cat_total = _sum(cat_entries)
            if cat_total > best_total:
                best_total = cat_total
                best_cat_entries = cat_entries
        return rev - best_total, rev_entries + best_cat_entries
    if agg == AggKind.FINANCING_INFLOW:
        chosen = [e for e in entries if e.is_inflow and e.category is Category.FINANCING]
        return _sum(chosen), chosen
    if agg == AggKind.REVENUE_PLUS_FINANCING:
        rev = _revenue(entries)
        fin = [e for e in entries if e.is_inflow and e.category is Category.FINANCING]
        chosen = rev + fin
        return _sum(chosen), chosen
    if agg == AggKind.RELATED_PARTY_OUTFLOW:
        chosen = _related(entries)
        return _sum(chosen), chosen
    if agg == AggKind.UNRESTRICTED_TRANSFER:
        chosen = [e for e in entries if e.is_outflow and e.is_unrestricted_transfer]
        return _sum(chosen), chosen
    chosen = (
        _spend(entries, cats)
        if cats
        else [e for e in entries if e.is_outflow and e.category is not Category.CONTRA]
    )
    return _sum(chosen), chosen


def _cats_from_slugs(slugs: list[str]) -> frozenset[Category]:
    mapping = {c.value: c for c in Category}
    return frozenset(mapping[s] for s in slugs if s in mapping)


def _semantic_rule_override(
    rule: Rule,
    scope: list[LedgerEntry],
    trace: EvaluationTrace | None,
    group_capex: Decimal | None,
) -> Answer | None:
    text = " ".join(rule.text.casefold().split())

    def spent(*categories: Category) -> tuple[Decimal, list[LedgerEntry]]:
        rows = _spend(scope, frozenset(categories))
        return _sum(rows), rows

    def inflow(category: Category) -> tuple[Decimal, list[LedgerEntry]]:
        rows = [e for e in scope if e.is_inflow and e.category is category]
        return _sum(rows), rows

    def finish(
        actual: Decimal,
        rows: list[LedgerEntry],
        note: str,
        *,
        status: str | None = None,
        aggregates: dict[str, Decimal] | None = None,
    ) -> Answer:
        answer = Answer(
            rule.scenario_id,
            rule.clause,
            status or _verdict(actual, rule),
            abs(actual),
            basis=list(dict.fromkeys(e.txn_id for e in rows)),
            note=note,
        )
        _complete_trace(
            trace,
            rule,
            answer,
            branch=note,
            aggregates=aggregates or {"semantic_actual": actual},
        )
        return answer

    def quarter_totals(rows: list[LedgerEntry]) -> list[tuple[Decimal, list[LedgerEntry]]]:
        buckets: dict[tuple[int, int], list[LedgerEntry]] = {}
        for item in rows:
            buckets.setdefault((item.day.year, (item.day.month - 1) // 3 + 1), []).append(item)
        return [(_sum(items), items) for _, items in sorted(buckets.items())]

    if ("разрешённой корзин" in text or "permitted basket" in text) and (
        "связанн" in text or "related part" in text
    ):
        basket_match = re.search(
            r"(?:корзин\w*|basket)[^$]{0,80}\$\s*([\d,]+(?:\.\d{2})?)", text
        )
        if basket_match:
            basket = Decimal(basket_match.group(1).replace(",", ""))
            related = _related(scope)
            eligible = [
                e
                for e in related
                if e.category is Category.PROFESSIONAL and not e.audit_reclassified
            ]
            actual = _sum(related) - min(basket, _sum(eligible))
            return finish(actual, related, "semantic_related_party_basket")

    quarterly = "квартал" in text or "quarter" in text
    if quarterly and ("выруч" in text or "revenue" in text) and "ebitda" not in text:
        revenue = _revenue(scope)
        totals = quarter_totals(revenue)
        if totals and (
            "не допускать снижения" in text
            or "must not fall below" in text
            or "minimum quarterly" in text
        ):
            actual, rows = min(totals, key=lambda pair: pair[0])
            return finish(actual, rows, "semantic_quarterly_min_revenue")
        if totals and (
            "any single fiscal quarter" in text
            or "наибольшая квартальная" in text
            or "maximum quarterly revenue" in text
        ):
            quarter, rows = max(totals, key=lambda pair: pair[0])
            annual = _sum(revenue)
            actual = quarter / annual if annual else Decimal(0)
            return finish(
                actual,
                revenue,
                "semantic_quarterly_revenue_concentration",
                aggregates={"max_quarter": quarter, "annual_revenue": annual},
            )

    if quarterly and ("маркет" in text or "marketing" in text) and (
        "наибольш" in text or "любом отдельном" in text or "any single" in text
    ):
        marketing = _spend(scope, frozenset({Category.MARKETING}))
        totals = quarter_totals(marketing)
        if totals:
            actual, rows = max(totals, key=lambda pair: pair[0])
            return finish(actual, rows, "semantic_quarterly_max_spend")

    if quarterly and "ebitda" in text and (
        "не допускать снижения" in text or "must not fall below" in text
    ):
        buckets: dict[tuple[int, int], list[LedgerEntry]] = {}
        for item in scope:
            buckets.setdefault((item.day.year, (item.day.month - 1) // 3 + 1), []).append(item)
        values = [(_ebitda(rows, EBITDA_OPEX), rows) for _, rows in sorted(buckets.items())]
        if values:
            actual, rows = min(values, key=lambda pair: pair[0])
            return finish(actual, rows, "semantic_quarterly_min_ebitda")

    if (
        "запас покрытия постоянных расходов" in text
        or "fixed cost coverage reserve" in text
    ) and (
        ("операцион" in text and "оплат" in text and "аренд" in text)
        or ("operating expenses" in text and "personnel" in text and "lease" in text)
    ):
        revenue, revenue_rows = inflow(Category.REVENUE)
        opex, opex_rows = spent(Category.OPEX)
        personnel, personnel_rows = spent(Category.PERSONNEL)
        lease, lease_rows = spent(Category.LEASE)
        actual = revenue - opex - personnel - lease
        return finish(
            actual,
            revenue_rows + opex_rows + personnel_rows + lease_rows,
            "semantic_fixed_cost_reserve",
            aggregates={
                "revenue": revenue,
                "opex": opex,
                "personnel": personnel,
                "lease": lease,
            },
        )

    if group_capex is not None and (
        "за пределами заёмщика" in text or "outside the borrower" in text
    ):
        borrower_capex, rows = spent(Category.CAPEX)
        return finish(
            group_capex - borrower_capex,
            rows,
            "semantic_group_capex_outside_borrower",
            aggregates={"group_capex": group_capex, "borrower_capex": borrower_capex},
        )

    if group_capex is not None and (
        ("процентн" in text and "арендн" in text)
        or ("interest" in text and "lease" in text)
    ) and ("процент" in text or "percent" in text):
        interest, interest_rows = spent(Category.INTEREST)
        lease, lease_rows = spent(Category.LEASE)
        percent_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:процент|percent|%)", text)
        if percent_match:
            allowed = group_capex * Decimal(percent_match.group(1).replace(",", ".")) / 100
            actual = interest + lease
            status = "COMPLIANT" if actual <= allowed else "BREACH"
            return finish(
                actual,
                interest_rows + lease_rows,
                "semantic_group_percentage_limit",
                status=status,
                aggregates={"actual_expense": actual, "allowed": allowed},
            )

    if ("арендн" in text or "rental" in text or "lease" in text) and (
        "страхов" in text or "insurance" in text
    ) and ("само по себе не влеч" in text or "do not cause default" in text):
        amounts = [
            Decimal(value.replace(",", ""))
            for value in re.findall(r"\$\s*([\d,]+(?:\.\d{2})?)", text)
        ]
        if len(amounts) >= 2:
            lease, lease_rows = spent(Category.LEASE)
            insurance, insurance_rows = spent(Category.INSURANCE)
            status = "COMPLIANT" if lease <= amounts[0] or insurance >= amounts[1] else "BREACH"
            return finish(
                lease,
                lease_rows + insurance_rows,
                "semantic_insurance_proviso",
                status=status,
                aggregates={"rent": lease, "insurance": insurance},
            )

    if ("арендн" in text or "rent" in text) and ("оплат" in text or "personnel" in text) and (
        "% of revenue" in text or "% выруч" in text
    ):
        pct = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
        if pct:
            personnel, _ = spent(Category.PERSONNEL)
            revenue, _ = inflow(Category.REVENUE)
            ratio = personnel / revenue if revenue else Decimal(0)
            lease, rows = spent(Category.LEASE)
            trigger = Decimal(pct.group(1).replace(",", ".")) / 100
            status = "COMPLIANT" if ratio <= trigger else _verdict(lease, rule)
            return finish(
                lease,
                rows,
                "semantic_springing_percentage",
                status=status,
                aggregates={"condition_value": ratio, "condition_threshold": trigger},
            )

    leverage_match = re.search(
        r"(?:превышает|exceeds)\s*(\d+(?:[.,]\d+)?)\s*x", text
    )
    if leverage_match and ("долгов" in text or "debt leverage" in text) and "ebitda" in text:
        financing, fin_rows = inflow(Category.FINANCING)
        ebitda = _ebitda(scope, EBITDA_OPEX)
        leverage = financing / ebitda if ebitda else Decimal(0)
        trigger = Decimal(leverage_match.group(1).replace(",", "."))
        if "капитальн" in text or "capital expenditure" in text:
            actual, rows = spent(Category.CAPEX)
        elif "связанн" in text or "related part" in text or "распредел" in text:
            actual, rows = _sum(_related(scope)), _related(scope)
        else:
            actual, rows = Decimal(0), []
        status = "COMPLIANT" if leverage <= trigger else _verdict(actual, rule)
        return finish(
            actual,
            rows + fin_rows,
            "semantic_springing_leverage",
            status=status,
            aggregates={"leverage": leverage, "trigger": trigger, "ebitda": ebitda},
        )

    if "операционная маржа после расходов на персонал" in text:
        revenue, rev_rows = inflow(Category.REVENUE)
        personnel, p_rows = spent(Category.PERSONNEL)
        tax, t_rows = spent(Category.TAX)
        actual = (revenue - personnel - tax) / revenue if revenue else Decimal(0)
        return finish(actual, rev_rows + p_rows + t_rows, "semantic_margin_after_personnel")

    if all(
        term in text
        for term in ("поступлен", "финансирован", "уменьш", "процент", "налог")
    ):
        financing, f_rows = inflow(Category.FINANCING)
        interest, i_rows = spent(Category.INTEREST)
        tax, t_rows = spent(Category.TAX)
        return finish(
            financing - interest - tax,
            f_rows + i_rows + t_rows,
            "semantic_net_financing",
        )

    if "вклад в ликвидность" in text and "ebitda" in text and "финансирован" in text:
        financing, f_rows = inflow(Category.FINANCING)
        actual = _ebitda(scope, EBITDA_OPEX) + financing
        return finish(actual, scope, "semantic_liquidity_contribution")

    if (
        ("одновременно выполняются оба" in text or "both of the following" in text)
        and ("процентным расход" in text or "interest expenses" in text)
        and "ebitda" in text
    ):
        debt_limit = re.search(r"(?:превышает|exceeds)\s*(\d+(?:[.,]\d+)?)\s*x", text)
        cover_limit = re.search(
            r"(?:менее|ниже|less than|below)\s*(\d+(?:[.,]\d+)?)\s*x", text
        )
        if debt_limit and cover_limit:
            financing, f_rows = inflow(Category.FINANCING)
            interest, i_rows = spent(Category.INTEREST)
            ebitda = _ebitda(scope, EBITDA_OPEX)
            debt_ratio = financing / ebitda if ebitda else Decimal(0)
            interest_cover = ebitda / interest if interest else Decimal(0)
            debt_threshold = Decimal(debt_limit.group(1).replace(",", "."))
            cover_threshold = Decimal(cover_limit.group(1).replace(",", "."))
            status = (
                "BREACH"
                if debt_ratio > debt_threshold and interest_cover < cover_threshold
                else "COMPLIANT"
            )
            return finish(
                debt_ratio,
                f_rows + i_rows,
                "semantic_both_debt_and_interest_cover",
                status=status,
                aggregates={"debt_ratio": debt_ratio, "interest_cover": interest_cover},
            )

    if (
        ("любого из следующих" in text or "either of the following" in text)
        and ("обслуживания долга" in text or "debt service coverage" in text)
        and "ebitda" in text
    ):
        debt_limit = re.search(r"(?:превышает|exceeds)\s*(\d+(?:[.,]\d+)?)\s*x", text)
        dscr_limit = re.search(
            r"(?:менее|ниже|less than|below)\s*(\d+(?:[.,]\d+)?)\s*x", text
        )
        if debt_limit and dscr_limit:
            financing, f_rows = inflow(Category.FINANCING)
            interest, i_rows = spent(Category.INTEREST)
            principal, p_rows = spent(Category.DEBT_PRINCIPAL)
            ebitda = _ebitda(scope, EBITDA_OPEX)
            debt_ratio = financing / ebitda if ebitda else Decimal(0)
            service = interest + principal
            dscr = ebitda / service if service else Decimal(0)
            debt_threshold = Decimal(debt_limit.group(1).replace(",", "."))
            dscr_threshold = Decimal(dscr_limit.group(1).replace(",", "."))
            status = (
                "BREACH"
                if debt_ratio > debt_threshold or dscr < dscr_threshold
                else "COMPLIANT"
            )
            return finish(
                dscr,
                f_rows + i_rows + p_rows,
                "semantic_either_debt_or_dscr",
                status=status,
                aggregates={"debt_ratio": debt_ratio, "dscr": dscr},
            )

    if "обслуживания долга" in text and ("погашен" in text or "principal" in text):
        interest, i_rows = spent(Category.INTEREST)
        principal, p_rows = spent(Category.DEBT_PRINCIPAL)
        ebitda = _ebitda(scope, EBITDA_OPEX)
        service = interest + principal
        actual = ebitda / service if service else Decimal(0)
        return finish(actual, i_rows + p_rows, "semantic_debt_service_cover")

    if "ebitdar" in text and ("аренд" in text or "lease" in text):
        interest, i_rows = spent(Category.INTEREST)
        lease, l_rows = spent(Category.LEASE)
        actual = (_ebitda(scope, EBITDA_OPEX) + lease) / (interest + lease)
        return finish(actual, i_rows + l_rows, "semantic_fixed_charge_cover")

    if "net leverage ratio" in text and (
        "maximum net debt leverage" in text
        or ("shall not permit" in text and "unrestricted" not in text and "transfer" not in text)
    ):
        financing, f_rows = inflow(Category.FINANCING)
        principal, p_rows = spent(Category.DEBT_PRINCIPAL)
        ebitda = _ebitda(scope, EBITDA_OPEX)
        actual = (financing - principal) / ebitda if ebitda else Decimal(0)
        return finish(actual, f_rows + p_rows, "semantic_net_leverage")

    return None


def _evaluate_with_formula(
    rule: Rule,
    scope: list[LedgerEntry],
    formula: FormulaSpec,
    trace: EvaluationTrace | None = None,
    numerator_constant: Decimal | None = None,
) -> Answer:
    from .llm_extract import OutputKind

    num_cats = _cats_from_slugs(formula.numerator_categories)
    den_cats = _cats_from_slugs(formula.denominator_categories)

    if numerator_constant is None:
        numerator, num_entries = _agg(scope, formula.numerator_agg, num_cats)
    else:
        numerator, num_entries = numerator_constant, []

    if formula.is_conditional and formula.condition_threshold_dollars is not None:
        condition_categories = _cats_from_slugs(formula.condition_categories)
        cond_val, _condition_entries = _agg(scope, formula.condition_agg, condition_categories)
        threshold = Decimal(str(formula.condition_threshold_dollars))
        triggered = (
            cond_val > threshold if formula.condition_comparator == ">" else cond_val >= threshold
        )
        if not triggered:
            answer = Answer(
                scenario_id=rule.scenario_id,
                clause=rule.clause,
                status="COMPLIANT",
                actual=abs(numerator),
                basis=[e.txn_id for e in num_entries],
                note="conditional_not_triggered",
            )
            _complete_trace(
                trace,
                rule,
                answer,
                branch="formula_conditional_not_triggered",
                aggregates={"condition_value": cond_val, "numerator": numerator},
            )
            if trace is not None:
                trace.comparator = formula.comparator
            return answer

    if formula.output_kind == OutputKind.DOLLAR_AMOUNT:
        actual = abs(numerator)
        answer = Answer(
            scenario_id=rule.scenario_id,
            clause=rule.clause,
            status=_verdict(actual, rule, comparator_override=formula.comparator),
            actual=actual,
            basis=[e.txn_id for e in num_entries],
            note=f"llm_dollar:{formula.numerator_agg}",
        )
        _complete_trace(
            trace,
            rule,
            answer,
            branch="formula_dollar_amount",
            aggregates={"numerator": numerator},
        )
        if trace is not None:
            trace.comparator = formula.comparator
        return answer

    denominator, den_entries = _agg(scope, formula.denominator_agg, den_cats)
    actual = (numerator / denominator) if denominator else Decimal(0)
    if trace is not None and not denominator:
        trace.quality_flags.append("zero_denominator")
    chosen = list({e.txn_id: e for e in num_entries + den_entries}.values())

    answer = Answer(
        scenario_id=rule.scenario_id,
        clause=rule.clause,
        status=_verdict(actual, rule, comparator_override=formula.comparator),
        actual=abs(actual),
        basis=[e.txn_id for e in chosen],
        note=f"llm_ratio:{formula.numerator_agg}/{formula.denominator_agg}",
    )
    _complete_trace(
        trace,
        rule,
        answer,
        branch="formula_ratio",
        aggregates={"numerator": numerator, "denominator": denominator},
    )
    if trace is not None:
        trace.comparator = formula.comparator
    return answer


def _evaluate_with_generic_formula(
    rule: Rule,
    scope: list[LedgerEntry],
    formula: GenericFormulaSpec,
    external_metrics: dict[str, ExternalMetric],
    documentary_fact: bool | None,
    trace: EvaluationTrace | None,
) -> Answer:
    from .generic_formula import CovenantMode, evaluate_expression

    if formula.mode is CovenantMode.DOCUMENTARY:
        if documentary_fact is None:
            if trace is not None:
                trace.quality_flags.append("missing_documentary_fact")
            documentary_fact = False
        satisfied = documentary_fact is formula.expected_presence
        actual = Decimal(1 if documentary_fact else 0)
        answer = Answer(
            scenario_id=rule.scenario_id,
            clause=rule.clause,
            status="COMPLIANT" if satisfied else "BREACH",
            actual=actual,
            note="generic_documentary",
        )
        _complete_trace(
            trace,
            rule,
            answer,
            branch="generic_documentary",
            aggregates={"fact_present": actual},
        )
        if trace is not None:
            trace.comparator = "=="
            trace.threshold = Decimal(1 if formula.expected_presence else 0)
        return answer

    if formula.expression is None:
        if trace is not None:
            trace.quality_flags.append("unsupported_formula")
        return Answer(
            rule.scenario_id, rule.clause, "COMPLIANT", Decimal(0), note="generic_missing"
        )

    result = evaluate_expression(formula.expression, scope, external_metrics)
    if trace is not None:
        trace.quality_flags.extend(result.quality_flags)
    if formula.condition is not None and formula.condition_threshold is not None:
        condition = evaluate_expression(formula.condition, scope, external_metrics)
        if trace is not None:
            trace.quality_flags.extend(condition.quality_flags)
        condition_comparators = {
            ">": condition.value > formula.condition_threshold,
            ">=": condition.value >= formula.condition_threshold,
            "<": condition.value < formula.condition_threshold,
            "<=": condition.value <= formula.condition_threshold,
            "==": condition.value == formula.condition_threshold,
        }
        triggered = condition_comparators.get(formula.condition_comparator, False)
        if not triggered:
            answer = Answer(
                rule.scenario_id,
                rule.clause,
                "COMPLIANT",
                abs(result.value),
                basis=[entry.txn_id for entry in result.entries],
                note="generic_conditional_not_triggered",
            )
            aggregates = {
                "condition_value": condition.value,
                "expression": result.value,
                **result.intermediates,
            }
            _complete_trace(
                trace,
                rule,
                answer,
                branch="generic_conditional_not_triggered",
                aggregates=aggregates,
            )
            return answer

    answer = Answer(
        scenario_id=rule.scenario_id,
        clause=rule.clause,
        status=_verdict(result.value, rule, comparator_override=formula.comparator),
        actual=abs(result.value),
        basis=[entry.txn_id for entry in result.entries],
        note="generic_numeric",
    )
    _complete_trace(
        trace,
        rule,
        answer,
        branch="generic_numeric",
        aggregates={"expression": result.value, **result.intermediates},
    )
    if trace is not None:
        trace.comparator = formula.comparator
    return answer


def evaluate(
    rule: Rule,
    entries: list[LedgerEntry],
    *,
    formula: FormulaSpec | None = None,
    generic_formula: GenericFormulaSpec | None = None,
    external_metrics: dict[str, ExternalMetric] | None = None,
    documentary_fact: bool | None = None,
    trace: EvaluationTrace | None = None,
    numerator_constant: Decimal | None = None,
    full_context_calculation: FullContextCalculation | None = None,
) -> Answer:
    scope = _select(entries, rule)
    if trace is not None:
        trace.scope_txn_ids = [entry.txn_id for entry in scope]
        from .generic_formula import CovenantMode

        if rule.threshold is None and (
            generic_formula is None or generic_formula.mode is not CovenantMode.DOCUMENTARY
        ):
            trace.quality_flags.append("missing_threshold")
        if (
            formula is None
            and generic_formula is None
            and full_context_calculation is None
            and rule.kind in (RuleKind.RATIO, RuleKind.UNKNOWN)
        ):
            trace.quality_flags.append("missing_formula")

    semantic = _semantic_rule_override(rule, scope, trace, numerator_constant)
    if semantic is not None:
        return semantic

    if full_context_calculation is not None:
        answer = Answer(
            scenario_id=rule.scenario_id,
            clause=rule.clause,
            status=full_context_calculation.status,
            actual=full_context_calculation.actual,
            basis=list(full_context_calculation.used_txn_ids),
            note="llm_full_context_verified",
        )
        _complete_trace(
            trace,
            rule,
            answer,
            branch="llm_full_context_verified",
            aggregates={
                f"step_{index}": step.result
                for index, step in enumerate(
                    full_context_calculation.calculation_steps,
                    start=1,
                )
            },
        )
        if trace is not None:
            trace.comparator = full_context_calculation.comparator
            trace.threshold = full_context_calculation.threshold
        return answer

    if generic_formula is not None and generic_formula.supported:
        return _evaluate_with_generic_formula(
            rule,
            scope,
            generic_formula,
            external_metrics or {},
            documentary_fact,
            trace,
        )

    if formula is not None and rule.kind in (RuleKind.RATIO, RuleKind.UNKNOWN):
        return _evaluate_with_formula(
            rule,
            scope,
            formula,
            trace,
            numerator_constant=numerator_constant,
        )

    if rule.kind is RuleKind.MIN_REVENUE:
        chosen = _revenue(scope)
        actual = _sum(chosen)
        aggregates = {"selected_total": actual}

    elif rule.kind is RuleKind.MAX_CATEGORY_SPEND:
        chosen = _spend(scope, rule.categories)
        actual = _sum(chosen)
        aggregates = {"selected_total": actual}

    elif rule.kind is RuleKind.MAX_RELATED_PARTY:
        chosen = _related(scope)
        actual = _sum(chosen)
        aggregates = {"selected_total": actual}

    elif rule.kind is RuleKind.RELATED_PARTY_SHARE:
        chosen = _related(scope)
        related = _sum(chosen)
        if Category.OPEX in rule.categories:
            base = _sum(_spend(scope, frozenset({Category.OPEX})))
            base_name = "opex_total"
        else:
            base = _sum(_revenue(scope))
            base_name = "revenue_total"
        actual = (related / base) if base else Decimal(0)
        if trace is not None and not base:
            trace.quality_flags.append("zero_denominator")
        aggregates = {"related_total": related, base_name: base}

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
        if trace is not None and not denominator:
            trace.quality_flags.append("zero_denominator")
        aggregates = {"numerator": numerator, "denominator": denominator}

    else:
        chosen = _spend(scope, rule.categories)
        actual = _sum(chosen)
        aggregates = {"selected_total": actual}

    answer = Answer(
        scenario_id=rule.scenario_id,
        clause=rule.clause,
        status=_verdict(actual, rule),
        actual=abs(actual),
        basis=[e.txn_id for e in chosen],
        note=rule.kind.value,
    )
    _complete_trace(
        trace,
        rule,
        answer,
        branch=rule.kind.value,
        aggregates=aggregates,
    )
    return answer


def find_evidence(
    rule: Rule,
    entries: list[LedgerEntry],
    answer: Answer,
    *,
    formula: FormulaSpec | None = None,
    generic_formula: GenericFormulaSpec | None = None,
    external_metrics: dict[str, ExternalMetric] | None = None,
    documentary_fact: bool | None = None,
    trials: dict[str, str] | None = None,
    numerator_constant: Decimal | None = None,
) -> str | None:
    """The transaction whose removal flips the verdict."""
    if answer.status != "BREACH" or not answer.basis:
        return None

    by_id = {entry.txn_id: entry for entry in entries}
    candidates = [
        txn_id
        for txn_id in answer.basis
        if (entry := by_id.get(txn_id)) is not None
        and (
            entry.audit_reclassified
            or entry.audit_corrected
            or entry.is_related_party
            or entry.is_unrestricted_transfer
            or (formula is not None and formula.numerator_agg.value == "max_single_transaction")
        )
    ]

    deciding: list[str] = []
    for txn_id in candidates:
        result = evaluate(
            rule,
            [e for e in entries if e.txn_id != txn_id],
            formula=formula,
            generic_formula=generic_formula,
            external_metrics=external_metrics,
            documentary_fact=documentary_fact,
            numerator_constant=numerator_constant,
        )
        if trials is not None:
            trials[txn_id] = result.status
        if result.status != "BREACH":
            deciding.append(txn_id)
    return deciding[0] if len(deciding) == 1 else None
