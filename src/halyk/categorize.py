"""Description text -> category.

The ledger has no category column, and every covenant is written in terms of
categories, so this module carries the whole gap between the two.

The one rule worth stating up front: **a positive amount is not revenue.**
P1's positive lines include a genuine sale of 6,842,117.53 and nine credits,
refunds, rebates and payroll sweep-backs that are larger or comparable. The
answer key for P1/6.2 is exactly the sale, so contra entries must be recognised
and kept out of revenue. Getting that wrong overstates revenue by 5x and, at a
5% tolerance, scores zero.

Matching is ordered: contra entries are tested before anything else, because
their wording ("utility rebate received", "insurance premium return") otherwise
looks like the very category they reverse.
"""

from __future__ import annotations

import re
from enum import StrEnum


class Category(StrEnum):
    REVENUE = "revenue"
    CAPEX = "capex"
    OPEX = "opex"
    LEASE = "lease"
    PERSONNEL = "personnel"
    UTILITIES = "utilities"
    TAX = "tax"
    INSURANCE = "insurance"
    INTEREST = "interest"
    MARKETING = "marketing"
    PROFESSIONAL = "professional"
    CONTRA = "contra"          # refunds, credits, reversals — reduce a cost
    UNKNOWN = "unknown"


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


# Tested first: these reverse a cost and must never count as revenue.
CONTRA = _rx(
    r"\brebate\b", r"\brefund", r"\bcredit received\b", r"\badjustment credit\b",
    r"\bcredit note\b", r"\breturned\b", r"\breturn\b", r"\brecovered\b",
    r"\bsweep back\b", r"\bswept back\b", r"\breversal\b", r"\breversed\b",
    r"\bwrite-back\b", r"\bclawback\b", r"\bunearned\b", r"\bdeposit returned\b",
    r"\bservice credit\b", r"\brate adjustment credit\b",
    r"қайтар", r"кері аудар", r"жеңілдік", r"түзету кредит",
)

# Genuine trading income.
REVENUE = _rx(
    r"\bsales?\b", r"\brevenue\b", r"\bcustomer (?:receipt|payment|settlement)",
    r"\bhandling and stevedoring\b", r"\bfreight (?:income|revenue)\b",
    r"\bthroughput (?:fee|income)\b", r"\btariff income\b", r"\bservice income\b",
    r"\bcontract income\b", r"\bmilestone (?:billing|payment)\b",
    r"сатудан түскен түсім", r"қызметтен түскен кіріс", r"клиент төлемі",
)

RULES: list[tuple[re.Pattern[str], Category]] = [
    (_rx(r"\binsurance\b", r"\bpremium\b", r"\bpolicy\b", r"\bunderwrit"),
     Category.INSURANCE),
    (_rx(r"сақтандыру", r"сақтандыру сыйлықақы"),
     Category.INSURANCE),

    (_rx(r"\bpayroll\b", r"\bsalar", r"\bwage", r"\bstaff\b", r"\bpersonnel\b",
         r"\bshift\b", r"\bovertime\b", r"\bseverance\b", r"\bbonus\b",
         r"\bpension\b", r"еңбекақы", r"жалақы", r"қызметкерлер"), Category.PERSONNEL),

    (_rx(r"\btax\b", r"\bvat\b", r"\bduty\b", r"\bexcise\b", r"\bcustoms\b",
         r"\blevy\b", r"\bmineral extraction\b", r"\bwithholding\b"), Category.TAX),
    (_rx(r"салық", r"қосылған құн салығы", r"кедендік баж"), Category.TAX),

    (_rx(r"\binterest\b", r"\bcoupon\b", r"\bloan (?:fee|charge)", r"\bfacility fee\b",
         r"\bfinance (?:cost|charge)"), Category.INTEREST),
    (_rx(r"пайыздық шығын", r"сыйақы бойынша шығын"), Category.INTEREST),

    (_rx(r"\brent\b", r"\blease\b", r"\bhire of\b", r"\bcharter\b", r"\btenanc",
         r"жалдау", r"лизинг"),
     Category.LEASE),

    (_rx(r"\belectricity\b", r"\bwater\b", r"\bgas supply\b", r"\bheating\b",
         r"\butility\b", r"\butilities\b", r"\bpower supply\b", r"\bmetering\b",
         r"\btelecom\b", r"\binternet\b", r"\bcommunication service"),
     Category.UTILITIES),
    (_rx(r"коммуналдық", r"электр энергия", r"сумен жабдықтау", r"жылумен жабдықтау"),
     Category.UTILITIES),

    (_rx(r"\bmarketing\b", r"\badvertis", r"\bmedia buy\b", r"\bpromotion",
         r"\bexhibition\b", r"\bbranding\b", r"\bsponsorship\b", r"\bnewsletter\b",
         r"\btrade press\b", r"\bdigital media\b", r"\bcampaign\b", r"маркетинг",
         r"жарнама"), Category.MARKETING),

    (_rx(r"\bshared services? payment\b", r"\bgroup services? payment\b",
         r"\baudit\b", r"\blegal\b", r"\bconsult", r"\badvisory\b", r"\bnotar",
         r"\bvaluation\b", r"\bappraisal\b", r"\bengineering bureau\b",
         r"\bremediation\b"),
     Category.PROFESSIONAL),
    (_rx(r"кәсіби қызмет", r"консультациялық", r"заңгерлік", r"аудиторлық"),
     Category.PROFESSIONAL),

    (_rx(r"\bcapital expenditure\b", r"\bcapex\b", r"\bconstruction\b",
         r"\bequipment purchase\b", r"\bpurchase of\b.*\bequipment\b",
         r"\bpurchase of\b.*\bcontrol system\b", r"\bfurnace control system\b",
         r"\btransfer of\b.*\bequipment\b",
         r"\bplant and machinery\b", r"\bacquisition of\b",
         r"\binstallation of\b", r"\bmodernisation\b", r"\bmodernization\b",
         r"\bupgrade of\b", r"\brefurbish", r"\boverhaul\b", r"\bfixed asset"),
     Category.CAPEX),
    (_rx(r"күрделі шығын", r"капиталдық шығын", r"жабдық сатып алу", r"негізгі құрал"),
     Category.CAPEX),
]


def categorize(description: str, *, is_inflow: bool = False) -> Category:
    """Classify one ledger line from its description.

    `is_inflow` only guards revenue: an inflow that matches no revenue wording
    is not assumed to be a sale, and an outflow is never revenue.
    """
    text = description or ""

    if CONTRA.search(text):
        return Category.CONTRA

    if REVENUE.search(text):
        return Category.REVENUE if is_inflow else Category.UNKNOWN

    for pattern, category in RULES:
        if pattern.search(text):
            return category

    # An unmatched outflow is still an operating cost; an unmatched inflow is not
    # revenue just because it is positive — that is the trap this module exists for.
    return Category.OPEX if not is_inflow else Category.UNKNOWN


#: Categories that make up operating expenditure when a covenant says "opex".
OPEX_LIKE = frozenset(
    {
        Category.OPEX,
        Category.UTILITIES,
        Category.MARKETING,
        Category.PROFESSIONAL,
        Category.PERSONNEL,
    }
)
