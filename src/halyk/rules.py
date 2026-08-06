"""Clause text -> executable rule.

Clause numbering is fixed (6.1, 6.2, 6.3) and doubles as the submission cell
key, so extraction is structural. What varies is the *kind* of test: across the
twelve agreements there are roughly nineteen distinct ones — category
aggregates, ratios, conditional (springing) tests, "revenue less the largest
overhead line".

The kind is decided from the clause heading, the threshold from its text. Both
are stated plainly enough in the documents that a model is not needed here; a
model earns its keep further along, where wording is genuinely ambiguous.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from .categorize import Category

CLAUSE_BLOCK = re.compile(
    r"Пункт\s+(6\.\d)\s*(.{0,120}?)[.\n](.*?)(?=Пункт\s+6\.\d|Статья\s+7|\Z)",
    re.S,
)
PERIOD = re.compile(r"с\s*(\d{4}-\d{2}-\d{2})\s*по\s*(\d{4}-\d{2}-\d{2})")
QUARTER = re.compile(
    r"(?:за|в)\s+(?:(\w+)\s+)?(?:финансов\w+\s+)?квартал\w*"
    r".*?оканчивающ\w+\s+(\d{4})-(\d{2})-(\d{2})",
    re.I | re.S,
)
QUARTER_WORDS = {"первый": 1, "второй": 2, "третий": 3, "четвёртый": 4, "четвертый": 4}
MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
RATIO = re.compile(r"(\d+(?:\.\d+)?)\s*x", re.I)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


class RuleKind(StrEnum):
    MIN_REVENUE = "min_revenue"
    MAX_CATEGORY_SPEND = "max_category_spend"
    MAX_RELATED_PARTY = "max_related_party"
    RELATED_PARTY_SHARE = "related_party_share"
    RATIO = "ratio"
    UNKNOWN = "unknown"


@dataclass
class Rule:
    scenario_id: str
    clause: str
    heading: str
    text: str
    kind: RuleKind
    comparator: str                  # ">=" for minimums, "<=" for ceilings
    threshold: Decimal | None
    period: tuple[date, date] | None
    categories: frozenset[Category] = frozenset()

    @property
    def is_minimum(self) -> bool:
        return self.comparator == ">="


HEADING_RULES: list[tuple[re.Pattern[str], RuleKind, str, frozenset[Category]]] = [
    (re.compile(r"выручк\w*\s+за\s+вычет", re.I),
     RuleKind.UNKNOWN, ">=", frozenset()),
    (re.compile(r"минимальн\w*\s+выручк", re.I),
     RuleKind.MIN_REVENUE, ">=", frozenset({Category.REVENUE})),
    (re.compile(r"максимальн\w*\s+платеж\w*\s+связанн", re.I),
     RuleKind.MAX_RELATED_PARTY, "<=", frozenset()),
    (re.compile(r"related-party payments as a proportion|дол\w*\s+платеж\w*\s+связанн", re.I),
     RuleKind.RELATED_PARTY_SHARE, "<=", frozenset()),
    (re.compile(r"максимальн\w*\s+расход\w*\s+по\s+категории", re.I),
     RuleKind.MAX_CATEGORY_SPEND, "<=", frozenset()),
    (re.compile(r"ratio|коэффициент|отношени|рентабельност|покрыти|leverage|intensity", re.I),
     RuleKind.RATIO, "<=", frozenset()),
]

#: Category words as they appear inside clause text.
CATEGORY_WORDS: list[tuple[re.Pattern[str], Category]] = [
    (re.compile(r"страхов", re.I), Category.INSURANCE),
    (re.compile(r"аренд|лизинг", re.I), Category.LEASE),
    (re.compile(r"персонал|оплат\w*\s+труда|фонд оплаты", re.I), Category.PERSONNEL),
    (re.compile(r"коммунальн", re.I), Category.UTILITIES),
    (re.compile(r"налог", re.I), Category.TAX),
    (re.compile(r"процент|купон", re.I), Category.INTEREST),
    (re.compile(r"маркетинг|реклам", re.I), Category.MARKETING),
    (re.compile(r"капитальн\w*\s+затрат", re.I), Category.CAPEX),
    (re.compile(r"операционн\w*\s+расход", re.I), Category.OPEX),
]


def _quarter_period(text: str) -> tuple[date, date] | None:
    m = QUARTER.search(text)
    if not m:
        return None
    word, year_s, month_s, day_s = m.groups()
    end = date(int(year_s), int(month_s), int(day_s))
    q = QUARTER_WORDS.get((word or "").lower(), 0)
    if not q:
        heading_q = re.search(r"(перв|втор|трет|четвёрт|четверт)\w*\s+квартал", text, re.I)
        if heading_q:
            prefix = heading_q.group(1).lower()
            for w, n in QUARTER_WORDS.items():
                if w.startswith(prefix):
                    q = n
                    break
    if q == 4:
        return (date(end.year, 10, 1), end)
    if q == 3:
        return (date(end.year, 7, 1), date(end.year, 9, 30))
    if q == 2:
        return (date(end.year, 4, 1), date(end.year, 6, 30))
    if q == 1:
        return (date(end.year, 1, 1), date(end.year, 3, 31))
    return None


def _threshold(text: str, kind: RuleKind = RuleKind.UNKNOWN) -> Decimal | None:
    ratio = RATIO.search(text)
    percent = PERCENT.search(text)
    money = MONEY.search(text)

    if kind in (RuleKind.RATIO, RuleKind.UNKNOWN):
        if ratio:
            return Decimal(ratio.group(1))
        if percent:
            return Decimal(percent.group(1)) / Decimal(100)
        if money:
            return Decimal(money.group(1).replace(",", ""))
    else:
        if money:
            return Decimal(money.group(1).replace(",", ""))
        if ratio:
            return Decimal(ratio.group(1))
        if percent:
            return Decimal(percent.group(1)) / Decimal(100)
    return None


def _kind(heading: str, body: str) -> tuple[RuleKind, str, frozenset[Category]]:
    for pattern, kind, comparator, categories in HEADING_RULES:
        if pattern.search(heading):
            return kind, comparator, categories
    for pattern, kind, comparator, categories in HEADING_RULES:
        if pattern.search(body[:300]):
            return kind, comparator, categories
    return RuleKind.UNKNOWN, "<=", frozenset()


def _categories(text: str) -> frozenset[Category]:
    found = {category for pattern, category in CATEGORY_WORDS if pattern.search(text)}
    return frozenset(found)


def extract_rules(scenario_id: str, agreement_text: str) -> dict[str, Rule]:
    """Pull 6.1-6.3 out of one credit agreement."""
    period_match = PERIOD.search(agreement_text)
    period = (
        (date.fromisoformat(period_match.group(1)), date.fromisoformat(period_match.group(2)))
        if period_match
        else None
    )

    rules: dict[str, Rule] = {}
    for clause, heading, body in CLAUSE_BLOCK.findall(agreement_text):
        heading = " ".join(heading.split())
        kind, comparator, categories = _kind(heading, body)
        if not categories:
            categories = _categories(body)
        clause_text = heading + " " + body
        clause_period = _quarter_period(clause_text)
        if clause_period is None:
            local_period = PERIOD.search(body)
            clause_period = (
                (date.fromisoformat(local_period.group(1)),
                 date.fromisoformat(local_period.group(2)))
                if local_period
                else period
            )
        rules[clause] = Rule(
            scenario_id=scenario_id,
            clause=clause,
            heading=heading,
            text=" ".join(body.split())[:1200],
            kind=kind,
            comparator=comparator,
            threshold=_threshold(body, kind),
            period=clause_period,
            categories=categories,
        )
    return rules
