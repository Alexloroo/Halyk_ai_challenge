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
    r"^\s*(?:(?:Пункт|Clause)\s+)?(6\s*\.\s*\d+)"
    r"(?:\s*[-–—]?\s*тарма(?:қ|ғы|қтың))?(?:\s*[.)]\s*)?\s*"
    r"(.{0,120}?)[.:\n](.*?)"
    r"(?=^\s*(?:(?:Пункт|Clause)\s+)?6\s*\.\s*\d+"
    r"(?:\s*[-–—]?\s*тарма(?:қ|ғы|қтың))?|"
    r"^\s*(?:(?:Статья|Пункт|Article|Section)\s+7|7\s*[-–—]?\s*бап)|\Z)",
    re.S | re.M | re.I,
)
PERIOD = re.compile(
    r"(?:с|from)\s*(\d{4}-\d{2}-\d{2})\s*(?:по|to|[-–—])\s*"
    r"(\d{4}-\d{2}-\d{2})",
    re.I,
)
KZ_PERIOD = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:\s*[-–—]?(?:ден|нан|тен)|\s+бастап)\s*"
    r"(\d{4}-\d{2}-\d{2})(?:\s*[-–—]?(?:ге|ға|ке|қа))?\s+дейін",
    re.I,
)
EU_PERIOD = re.compile(
    r"(?:(?:с|from)\s*)?(\d{2}[./]\d{2}[./]\d{4})\s*(?:по|to|[-–—])\s*"
    r"(\d{2}[./]\d{2}[./]\d{4})",
    re.I,
)
QUARTER = re.compile(
    r"(?:за|в)\s+(?:(\w+)\s+)?(?:финансов\w+\s+)?квартал\w*"
    r".*?оканчивающ\w+\s+(\d{4})-(\d{2})-(\d{2})",
    re.I | re.S,
)
QUARTER_WORDS = {"первый": 1, "второй": 2, "третий": 3, "четвёртый": 4, "четвертый": 4}
NUMBER = r"\d(?:[\d\s\u00a0]*\d)?(?:[.,]\d+)*"
MONEY = re.compile(
    rf"(?:\$|US\$)\s*({NUMBER})|"
    rf"(?:USD|доллар(?:ов|а|ы)?\s+США)\s*({NUMBER})|"
    rf"({NUMBER})\s*(?:USD|US\s*dollars?|доллар(?:ов|а|ы)?\s+США)",
    re.I,
)
RATIO = re.compile(r"(\d+(?:[.,]\d+)?)\s*[xх×]", re.I)
PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


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
    comparator: str  # ">=" for minimums, "<=" for ceilings
    threshold: Decimal | None
    period: tuple[date, date] | None
    categories: frozenset[Category] = frozenset()

    @property
    def is_minimum(self) -> bool:
        return self.comparator == ">="


HEADING_RULES: list[tuple[re.Pattern[str], RuleKind, str, frozenset[Category]]] = [
    (re.compile(r"выручк\w*\s+за\s+вычет", re.I), RuleKind.UNKNOWN, ">=", frozenset()),
    (
        re.compile(r"минимальн\w*\s+выручк", re.I),
        RuleKind.MIN_REVENUE,
        ">=",
        frozenset({Category.REVENUE}),
    ),
    (
        re.compile(r"minimum\s+revenue|revenue\s+minimum", re.I),
        RuleKind.MIN_REVENUE,
        ">=",
        frozenset({Category.REVENUE}),
    ),
    (
        re.compile(r"максимальн\w*\s+платеж\w*\s+связанн", re.I),
        RuleKind.MAX_RELATED_PARTY,
        "<=",
        frozenset(),
    ),
    (
        re.compile(r"maximum\s+related[- ]party\s+payments?\b(?!\s+as\s+a\s+proportion)", re.I),
        RuleKind.MAX_RELATED_PARTY,
        "<=",
        frozenset(),
    ),
    (
        re.compile(r"related-party payments as a proportion|дол\w*\s+платеж\w*\s+связанн", re.I),
        RuleKind.RELATED_PARTY_SHARE,
        "<=",
        frozenset(),
    ),
    (
        re.compile(r"максимальн\w*\s+расход\w*\s+по\s+категории", re.I),
        RuleKind.MAX_CATEGORY_SPEND,
        "<=",
        frozenset(),
    ),
    (
        re.compile(r"maximum\s+(?:category\s+)?spend|category\s+spend\s+ceiling", re.I),
        RuleKind.MAX_CATEGORY_SPEND,
        "<=",
        frozenset(),
    ),
    (
        re.compile(r"ratio|коэффициент|отношени|рентабельност|покрыти|leverage|intensity", re.I),
        RuleKind.RATIO,
        "<=",
        frozenset(),
    ),
    (
        re.compile(r"коэффициент|арақатынас|қатынас|рентабельділік|өтімділік|жабу", re.I),
        RuleKind.RATIO,
        "<=",
        frozenset(),
    ),
    (
        re.compile(
            r"ең\s+төменгі.*(?:түсім|кіріс)|(?:түсім|кіріс)\w*.*ең\s+төменгі|"
            r"минималды.*(?:түсім|кіріс)",
            re.I,
        ),
        RuleKind.MIN_REVENUE,
        ">=",
        frozenset({Category.REVENUE}),
    ),
    (
        re.compile(r"байланысты\s+тарап.*(?:ең\s+жоғары|максималды)", re.I),
        RuleKind.MAX_RELATED_PARTY,
        "<=",
        frozenset(),
    ),
    (
        re.compile(r"санат\s+бойынша.*(?:ең\s+жоғары|максималды).*шығын", re.I),
        RuleKind.MAX_CATEGORY_SPEND,
        "<=",
        frozenset(),
    ),
]

#: Category words as they appear inside clause text.
CATEGORY_WORDS: list[tuple[re.Pattern[str], Category]] = [
    (re.compile(r"страхов|\binsurance\b", re.I), Category.INSURANCE),
    (re.compile(r"аренд|лизинг|\blease\b", re.I), Category.LEASE),
    (
        re.compile(r"персонал|оплат\w*\s+труда|фонд оплаты|\bpersonnel\b|\bpayroll\b", re.I),
        Category.PERSONNEL,
    ),
    (re.compile(r"коммунальн|\butilit(?:y|ies)\b", re.I), Category.UTILITIES),
    (re.compile(r"налог|\btax(?:es)?\b", re.I), Category.TAX),
    (re.compile(r"процент|купон|\binterest\b", re.I), Category.INTEREST),
    (re.compile(r"маркетинг|реклам|\bmarketing\b", re.I), Category.MARKETING),
    (re.compile(r"капитальн\w*\s+затрат|\bcapex\b|\bcapital expenditure", re.I), Category.CAPEX),
    (re.compile(r"операционн\w*\s+расход|\bopex\b|\boperating expense", re.I), Category.OPEX),
    (
        re.compile(
            r"консультац|профессиональн|\bprofessional(?: services?)?\b|"
            r"\bconsult(?:ing|ancy)?\b|\badvisory\b",
            re.I,
        ),
        Category.PROFESSIONAL,
    ),
    (re.compile(r"выручк|\brevenue\b", re.I), Category.REVENUE),
    (re.compile(r"сақтандыру", re.I), Category.INSURANCE),
    (re.compile(r"жалдау|лизинг", re.I), Category.LEASE),
    (re.compile(r"персонал|еңбекақы|жалақы|қызметкер", re.I), Category.PERSONNEL),
    (re.compile(r"коммуналдық", re.I), Category.UTILITIES),
    (re.compile(r"салық", re.I), Category.TAX),
    (re.compile(r"пайыздық|сыйақы\s+бойынша", re.I), Category.INTEREST),
    (re.compile(r"маркетинг|жарнама", re.I), Category.MARKETING),
    (re.compile(r"күрделі\s+шығын|капиталдық\s+шығын", re.I), Category.CAPEX),
    (re.compile(r"операциялық\s+шығын", re.I), Category.OPEX),
    (re.compile(r"кәсіби|консультациялық|аудиторлық|заңгерлік", re.I), Category.PROFESSIONAL),
    (re.compile(r"түсім|кіріс", re.I), Category.REVENUE),
]

MINIMUM_WORDS = re.compile(
    r"не\s+менее|не\s+ниже|at\s+least|not\s+less\s+than|must\s+not\s+fall\s+below|"
    r"кем\s+емес|төмен\s+емес|кем\s+болма\w*\s+тиіс",
    re.I,
)
MAXIMUM_WORDS = re.compile(
    r"не\s+выше|не\s+более|не\s+превыш|must\s+not\s+exceed|not\s+more\s+than|"
    r"аспау|артық\s+емес|жоғары\s+емес",
    re.I,
)


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


def _decimal_number(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def _money_number(text: str) -> Decimal:
    compact = re.sub(r"[\s\u00a0]", "", text)
    if "," in compact and "." in compact:
        decimal_separator = "," if compact.rfind(",") > compact.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        compact = compact.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in compact:
        pieces = compact.split(",")
        compact = ".".join(pieces) if len(pieces) == 2 and len(pieces[-1]) <= 2 else "".join(pieces)
    elif compact.count(".") > 1:
        pieces = compact.split(".")
        compact = (
            "".join(pieces[:-1]) + "." + pieces[-1] if len(pieces[-1]) <= 2 else "".join(pieces)
        )
    return Decimal(compact)


def _threshold(text: str, kind: RuleKind = RuleKind.UNKNOWN) -> Decimal | None:
    ratio = RATIO.search(text)
    percent = PERCENT.search(text)
    money = MONEY.search(text)

    if kind in (RuleKind.RATIO, RuleKind.UNKNOWN):
        if ratio:
            return _decimal_number(ratio.group(1))
        if percent:
            return _decimal_number(percent.group(1)) / Decimal(100)
        if money:
            return _money_number(next(group for group in money.groups() if group))
    else:
        if money:
            return _money_number(next(group for group in money.groups() if group))
        if ratio:
            return _decimal_number(ratio.group(1))
        if percent:
            return _decimal_number(percent.group(1)) / Decimal(100)
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


def _period(text: str) -> tuple[date, date] | None:
    match = PERIOD.search(text) or KZ_PERIOD.search(text)
    if match:
        return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
    match = EU_PERIOD.search(text)
    if not match:
        return None
    return tuple(date(int(raw[-4:]), int(raw[3:5]), int(raw[:2])) for raw in match.groups())


def extract_rules(scenario_id: str, agreement_text: str) -> dict[str, Rule]:
    """Pull 6.1-6.3 out of one credit agreement."""
    period = _period(agreement_text)

    rules: dict[str, Rule] = {}
    for clause, heading, body in CLAUSE_BLOCK.findall(agreement_text):
        clause = re.sub(r"\s+", "", clause)
        heading = " ".join(heading.split())
        kind, comparator, categories = _kind(heading, body)
        clause_text = heading + " " + body
        if MINIMUM_WORDS.search(clause_text):
            comparator = ">="
        elif MAXIMUM_WORDS.search(clause_text):
            comparator = "<="
        if not categories:
            categories = _categories(clause_text)
        clause_period = _quarter_period(clause_text)
        if clause_period is None:
            clause_period = _period(body) or period
        rules[clause] = Rule(
            scenario_id=scenario_id,
            clause=clause,
            heading=heading,
            text=" ".join(body.split())[:4000],
            kind=kind,
            comparator=comparator,
            threshold=_threshold(body, kind),
            period=clause_period,
            categories=categories,
        )
    return rules


def rule_from_evidence(
    scenario_id: str,
    clause: str,
    heading: str,
    text: str,
    agreement_text: str,
    *,
    kind: RuleKind,
    comparator: str,
    categories: list[Category],
) -> Rule:
    """Build an executable rule from validated agreement evidence."""
    resolved_categories = frozenset(categories) or _categories(f"{heading} {text}")
    period = _quarter_period(text) or _period(text) or _period(agreement_text)
    return Rule(
        scenario_id=scenario_id,
        clause=clause,
        heading=" ".join(heading.split()),
        text=" ".join(text.split())[:4000],
        kind=kind,
        comparator=comparator,
        threshold=_threshold(text, kind),
        period=period,
        categories=resolved_categories,
    )
