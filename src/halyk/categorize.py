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
from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    REVENUE = "revenue"
    FINANCING = "financing"
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
    DEBT_PRINCIPAL = "debt_principal"
    ASSET_TRANSFER = "asset_transfer"
    DISTRIBUTION = "distribution"
    CONTRA = "contra"
    UNKNOWN = "unknown"


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


CONTRA = _rx(
    r"\brebate\b",
    r"\brefund",
    r"\bcredit received\b",
    r"\badjustment credit\b",
    r"\bcredit note\b",
    r"\breturned\b",
    r"\breturn\b",
    r"\brecovered\b",
    r"\bsweep back\b",
    r"\bswept back\b",
    r"\breversal\b",
    r"\breversed\b",
    r"\bwrite-back\b",
    r"\bclawback\b",
    r"\bunearned\b",
    r"\bdeposit returned\b",
    r"\bservice credit\b",
    r"\brate adjustment credit\b",
    r"қайтар",
    r"кері аудар",
    r"жеңілдік",
    r"түзету кредит",
)

REVENUE = _rx(
    r"\bsales?\b",
    r"\brevenue\b",
    r"\bcustomer (?:receipt|payment|settlement)",
    r"\bhandling and stevedoring\b",
    r"\bfreight (?:income|revenue)\b",
    r"\bthroughput (?:fee|income)\b",
    r"\btariff income\b",
    r"\bservice income\b",
    r"\bcontract income\b",
    r"\bmilestone (?:billing|payment)\b",
    r"сатудан түскен түсім",
    r"қызметтен түскен кіріс",
    r"клиент төлемі",
)

FINANCING = _rx(
    r"\b(?:term\s+)?loan\s+(?:facility\s+)?(?:drawdown|proceeds|disbursement)\b",
    r"\bfacility\s+(?:drawdown|proceeds|disbursement)\b",
    r"\b(?:revolver|revolving|credit)\s+facility\s+(?:drawdown|proceeds)\b",
    r"\bborrowing\s+proceeds\b",
    r"\bfinancing\s+(?:receipts|proceeds)\b",
    r"\bpromissory\s+note\s+proceeds\b",
    r"поступлен\w+\s+по\s+финансирован",
    r"кредитн\w+\s+средств\w+\s+получен",
    r"қарыз\s+қаражат\w*\s+түсім",
    r"қаржыландырудан\s+түскен\s+қаражат",
)

DEBT_PRINCIPAL = _rx(
    r"\b(?:term\s+)?loan\s+principal\s+(?:repayment|payment)\b",
    r"\b(?:debt|borrowing)\s+principal\s+(?:repayment|payment)\b",
    r"\bprincipal\s+repayment\b",
    r"погашен\w+\s+основн\w+\s+(?:долг|сумм)",
    r"негізгі\s+борышты\s+өтеу",
)
ASSET_TRANSFER = _rx(
    r"\b(?:asset|equipment|machinery)\s+transfer\b",
    r"\btransfer\s+of\s+(?:an?\s+)?(?:asset|equipment|machinery)\b",
    r"передач\w+\s+(?:актив|оборудован)",
    r"активтерді\s+беру",
)
DISTRIBUTION = _rx(
    r"\b(?:intercompany\s+)?distribution\b",
    r"\bdividend\b",
    r"\brestricted\s+payment\b",
    r"распределен\w+\s+(?:прибы|доход)",
    r"дивиденд",
    r"бөлінбеген\s+пайда",
)

GENERIC_OPEX = _rx(
    r"\boperating costs?\b",
    r"\boperating and maintenance\b",
    r"\bservicing(?: contract)?\b",
    r"\bmaintenance\b",
    r"\bsupport payment\b",
    r"\bmanagement services?\b",
    r"\bprocurement payment\b",
    r"\bwarehouse service\b",
    r"\bretainer fee\b",
    r"\brental payment\b",
    r"\bcleaning and clearance works?\b",
)

RULES: list[tuple[re.Pattern[str], Category]] = [
    (_rx(r"\binsurance\b", r"\bpremium\b", r"\bpolicy\b", r"\bunderwrit"), Category.INSURANCE),
    (_rx(r"сақтандыру", r"сақтандыру сыйлықақы"), Category.INSURANCE),
    (
        _rx(
            r"\bpayroll\b",
            r"\bsalar",
            r"\bwage",
            r"\bstaff\b",
            r"\bpersonnel\b",
            r"\bshift\b",
            r"\bovertime\b",
            r"\bseverance\b",
            r"\bbonus\b",
            r"\bpension\b",
            r"еңбекақы",
            r"жалақы",
            r"қызметкерлер",
        ),
        Category.PERSONNEL,
    ),
    (
        _rx(
            r"\btax\b",
            r"\bvat\b",
            r"\bduty\b",
            r"\bexcise\b",
            r"\bcustoms\b",
            r"\blevy\b",
            r"\bmineral extraction\b",
            r"\bwithholding\b",
        ),
        Category.TAX,
    ),
    (_rx(r"салық", r"қосылған құн салығы", r"кедендік баж"), Category.TAX),
    (
        _rx(
            r"\binterest\b",
            r"\bcoupon\b",
            r"\bloan (?:fee|charge)",
            r"\bfacility fee\b",
            r"\bfinance (?:cost|charge)",
        ),
        Category.INTEREST,
    ),
    (_rx(r"пайыздық шығын", r"сыйақы бойынша шығын"), Category.INTEREST),
    (
        _rx(
            r"\brent\b",
            r"\blease\b",
            r"\bhire of\b",
            r"\bcharter\b",
            r"\btenanc",
            r"жалдау",
            r"лизинг",
        ),
        Category.LEASE,
    ),
    (
        _rx(
            r"\belectricity\b",
            r"\bwater\b",
            r"\bgas supply\b",
            r"\bheating\b",
            r"\butility\b",
            r"\butilities\b",
            r"\bpower supply\b",
            r"\bmetering\b",
            r"\btelecom\b",
            r"\binternet\b",
            r"\bcommunication service",
        ),
        Category.UTILITIES,
    ),
    (
        _rx(r"коммуналдық", r"электр энергия", r"сумен жабдықтау", r"жылумен жабдықтау"),
        Category.UTILITIES,
    ),
    (
        _rx(
            r"\bmarketing\b",
            r"\badvertis",
            r"\bmedia buy\b",
            r"\bpromotion",
            r"\bexhibition\b",
            r"\bbranding\b",
            r"\bsponsorship\b",
            r"\bnewsletter\b",
            r"\btrade press\b",
            r"\bdigital media\b",
            r"\bcampaign\b",
            r"маркетинг",
            r"жарнама",
        ),
        Category.MARKETING,
    ),
    (
        _rx(
            r"\bshared services? payment\b",
            r"\bgroup services? payment\b",
            r"\baudit\b",
            r"\blegal\b",
            r"\bconsult",
            r"\badvisory\b",
            r"\bnotar",
            r"\bvaluation\b",
            r"\bappraisal\b",
            r"\bengineering bureau\b",
            r"\bremediation\b",
        ),
        Category.PROFESSIONAL,
    ),
    (_rx(r"кәсіби қызмет", r"консультациялық", r"заңгерлік", r"аудиторлық"), Category.PROFESSIONAL),
    (
        _rx(
            r"\bcapital expenditure\b",
            r"\bcapex\b",
            r"\bconstruction\b",
            r"\bequipment purchase\b",
            r"\bpurchase of\b.*\bequipment\b",
            r"\bpurchase of\b.*\bcontrol system\b",
            r"\bfurnace control system\b",
            r"\btransfer of\b.*\bequipment\b",
            r"\bplant and machinery\b",
            r"\bacquisition of\b",
            r"\binstallation of\b",
            r"\bmodernisation\b",
            r"\bmodernization\b",
            r"\bupgrade of\b",
            r"\brefurbish",
            r"\boverhaul\b",
            r"\bfixed asset",
        ),
        Category.CAPEX,
    ),
    (
        _rx(r"күрделі шығын", r"капиталдық шығын", r"жабдық сатып алу", r"негізгі құрал"),
        Category.CAPEX,
    ),
]


@dataclass(frozen=True)
class CategorizationAssessment:
    category: Category
    candidates: tuple[Category, ...]
    reason: str
    needs_llm: bool


def assess_category(
    description: str,
    *,
    is_inflow: bool = False,
) -> CategorizationAssessment:
    """Classify high-confidence text and identify cases needing semantic fallback."""
    text = description or ""

    if CONTRA.search(text):
        return CategorizationAssessment(Category.CONTRA, (Category.CONTRA,), "contra", False)

    if is_inflow and FINANCING.search(text):
        return CategorizationAssessment(
            Category.FINANCING, (Category.FINANCING,), "financing", False
        )

    if is_inflow and REVENUE.search(text):
        return CategorizationAssessment(Category.REVENUE, (Category.REVENUE,), "revenue", False)

    if not is_inflow and DEBT_PRINCIPAL.search(text):
        return CategorizationAssessment(
            Category.DEBT_PRINCIPAL,
            (Category.DEBT_PRINCIPAL,),
            "debt_principal",
            False,
        )
    if not is_inflow and ASSET_TRANSFER.search(text):
        return CategorizationAssessment(
            Category.ASSET_TRANSFER,
            (Category.ASSET_TRANSFER,),
            "asset_transfer",
            False,
        )
    if not is_inflow and DISTRIBUTION.search(text):
        return CategorizationAssessment(
            Category.DISTRIBUTION,
            (Category.DISTRIBUTION,),
            "distribution",
            False,
        )

    candidates = tuple(
        dict.fromkeys(category for pattern, category in RULES if pattern.search(text))
    )
    if is_inflow:
        if candidates:
            return CategorizationAssessment(
                candidates[0], candidates, "inflow_nontrading_match", False
            )
        return CategorizationAssessment(Category.UNKNOWN, (), "unmatched_inflow", True)

    if len(candidates) == 1:
        return CategorizationAssessment(candidates[0], candidates, "single_match", False)
    if len(candidates) > 1:
        if Category.CAPEX in candidates:
            return CategorizationAssessment(Category.CAPEX, candidates, "specific_capex", False)
        return CategorizationAssessment(candidates[0], candidates, "ordered_match", False)
    if GENERIC_OPEX.search(text):
        return CategorizationAssessment(Category.OPEX, (Category.OPEX,), "generic_opex", False)
    return CategorizationAssessment(Category.OPEX, (), "default_opex", True)


def categorize(description: str, *, is_inflow: bool = False) -> Category:
    """Classify one ledger line from its description.

    `is_inflow` only guards revenue: an inflow that matches no revenue wording
    is not assumed to be a sale, and an outflow is never revenue.
    """
    return assess_category(description, is_inflow=is_inflow).category


OPEX_LIKE = frozenset(
    {
        Category.OPEX,
        Category.UTILITIES,
        Category.MARKETING,
        Category.PROFESSIONAL,
        Category.PERSONNEL,
    }
)
