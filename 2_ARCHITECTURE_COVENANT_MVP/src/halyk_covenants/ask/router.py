"""Route a free-form question to a (borrower, covenant, date) triple.

Everything the pipeline needs is already compiled and stored; the missing piece
was a way to point at it without knowing internal identifiers. This layer does
that resolution deterministically — no LLM — so a wrong route is inspectable
rather than mysterious.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

from rank_bm25 import BM25Okapi

from halyk_covenants.borrowers import BorrowerClaim, BorrowerResolver
from halyk_covenants.domain import Borrower, CovenantSpec

_MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
    "january": 1,
    "february": 3,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_TOKEN = re.compile(r"[\wЀ-ӿ]+", re.UNICODE)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_YEAR = re.compile(r"\b([а-яa-z]{3,10})\w*\s+(\d{4})\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")


@dataclass
class RouteStep:
    """One resolution decision, kept so the answer can explain itself."""

    what: str
    value: str
    how: str


@dataclass
class Route:
    borrower_id: str | None = None
    borrower_name: str | None = None
    covenant: CovenantSpec | None = None
    covenant_score: float = 0.0
    at_date: date | None = None
    # Inclusive bounds when the question names a month or a year. A covenant with
    # its own time window ignores this; one without a window uses it, so that
    # "в апреле" is not silently dropped.
    period: tuple[date, date] | None = None
    period_applied: bool = False
    steps: list[RouteStep] = field(default_factory=list)
    alternatives: list[tuple[CovenantSpec, float]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def tokenize(text: str) -> list[str]:
    return [t.casefold() for t in _TOKEN.findall(text)]


def _name_candidates(question: str) -> list[str]:
    """Word n-grams that could be a company name, longest first.

    A question rarely marks where the name starts and ends, so every 1..4-word
    window is offered to the resolver and the longest match wins.
    """
    words = [w for w in _TOKEN.findall(question) if len(w) > 1]
    grams: list[str] = []
    for size in (4, 3, 2, 1):
        for start in range(len(words) - size + 1):
            grams.append(" ".join(words[start : start + size]))
    return grams


def resolve_borrower(question: str, borrowers: list[Borrower]) -> tuple[str | None, str, str]:
    """Return (borrower_id, display_name, how).

    Identifiers and ids are matched literally; names go through the project's
    BorrowerResolver, which normalizes and falls back to fuzzy matching — so
    "Alpha Trade" still finds an entry stored as "ALFA TRADE LLP".
    """
    lowered = question.casefold()

    for borrower in borrowers:
        for kind, value in borrower.identifiers.items():
            if value and str(value).casefold() in lowered:
                return borrower.borrower_id, borrower.canonical_name, f"по идентификатору {kind}"
        if re.search(rf"(?<![\w-]){re.escape(borrower.borrower_id)}(?![\w-])", question, re.I):
            return borrower.borrower_id, borrower.canonical_name, "по идентификатору в тексте"

    resolver = BorrowerResolver(borrowers)
    for candidate in _name_candidates(question):
        resolution = resolver.resolve(BorrowerClaim(name=candidate))
        if resolution.status.startswith("resolved_") and len(resolution.borrower_ids) == 1:
            borrower_id = resolution.borrower_ids[0]
            match = next(b for b in borrowers if b.borrower_id == borrower_id)
            how = {
                "resolved_exact": f"по названию «{candidate}»",
                "resolved_alias": f"по псевдониму «{candidate}»",
                "resolved_fuzzy": f"нечёткое совпадение с «{candidate}»",
            }.get(resolution.status, f"по «{candidate}»")
            return borrower_id, match.canonical_name, how

    return None, "", "не найден"


def resolve_date(
    question: str, default: date
) -> tuple[date, str, tuple[date, date] | None]:
    """Return (evaluation date, how, inclusive period).

    The period is set only when the question names a month or a year, because
    only then does the user mean a span rather than a point in time.
    """
    iso = _ISO_DATE.search(question)
    if iso:
        return date(int(iso[1]), int(iso[2]), int(iso[3])), "дата указана явно", None

    year_match = _YEAR.search(question)
    for match in _MONTH_YEAR.finditer(question):
        word = match[1].casefold()
        for stem, month in _MONTHS.items():
            if word.startswith(stem):
                year = int(match[2])
                last = calendar.monthrange(year, month)[1]
                end = date(year, month, last)
                return end, f"конец месяца «{match[1]}»", (date(year, month, 1), end)

    lowered = question.casefold()
    for stem, month in sorted(_MONTHS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{stem}\w*", lowered):
            year = int(year_match[1]) if year_match else default.year
            last = calendar.monthrange(year, month)[1]
            end = date(year, month, last)
            return end, f"конец месяца, год {year}", (date(year, month, 1), end)

    if year_match:
        year = int(year_match[1])
        end = date(year, 12, 31)
        return end, "конец указанного года", (date(year, 1, 1), end)
    return default, "дата не указана, взята по умолчанию", None


def rank_covenants(
    question: str,
    specs: list[CovenantSpec],
    borrower_id: str | None,
) -> list[tuple[CovenantSpec, float]]:
    """Score covenants against the question by lexical overlap with their clause text."""
    scoped = [s for s in specs if borrower_id is None or borrower_id in s.borrower_ids]
    if not scoped:
        return []
    if len(scoped) == 1:
        return [(scoped[0], 1.0)]

    corpus = [tokenize(f"{s.raw_text} {s.covenant_id} {s.metric.metric_type}") for s in scoped]
    scores = BM25Okapi(corpus).get_scores(tokenize(question))
    top = max(scores) if len(scores) else 0.0
    normalized = [
        (s, float(v) / top if top > 0 else 0.0) for s, v in zip(scoped, scores, strict=True)
    ]
    normalized.sort(key=lambda item: (-item[1], item[0].covenant_id))
    return normalized


def route_question(
    question: str,
    borrowers: list[Borrower],
    specs: list[CovenantSpec],
    *,
    default_date: date,
    borrower_id: str | None = None,
    covenant_id: str | None = None,
) -> Route:
    route = Route()

    if borrower_id:
        route.borrower_id = borrower_id
        match = next((b for b in borrowers if b.borrower_id == borrower_id), None)
        route.borrower_name = match.canonical_name if match else ""
        route.steps.append(RouteStep("Заёмщик", borrower_id, "задан флагом"))
    else:
        found, name, how = resolve_borrower(question, borrowers)
        route.borrower_id, route.borrower_name = found, name
        route.steps.append(RouteStep("Заёмщик", f"{found or '—'} ({name})" if found else "—", how))
        if found is None:
            route.problems.append(
                "Заёмщик не распознан. Укажите его через --borrower или назовите в вопросе."
            )

    at_date, how, period = resolve_date(question, default_date)
    route.at_date = at_date
    route.period = period
    route.steps.append(RouteStep("Дата оценки", at_date.isoformat(), how))

    if covenant_id:
        match = next((s for s in specs if s.covenant_id == covenant_id), None)
        route.covenant, route.covenant_score = match, 1.0
        route.steps.append(RouteStep("Ковенант", covenant_id, "задан флагом"))
        if match is None:
            route.problems.append(f"Ковенант {covenant_id} не найден в реестре.")
        return route

    ranked = rank_covenants(question, specs, route.borrower_id)
    if not ranked:
        route.problems.append("Для этого заёмщика нет скомпилированных ковенантов.")
        route.steps.append(RouteStep("Ковенант", "—", "кандидатов нет"))
        return route

    route.covenant, route.covenant_score = ranked[0]
    route.alternatives = ranked[1:4]
    how = (
        "единственный ковенант заёмщика"
        if len(ranked) == 1
        else (f"по смыслу вопроса, совпадение {route.covenant_score:.2f}")
    )
    route.steps.append(RouteStep("Ковенант", route.covenant.covenant_id, how))

    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.15:
        route.problems.append(
            "Несколько ковенантов подходят почти одинаково — "
            "уточните вопрос или задайте --covenant."
        )
    return route
