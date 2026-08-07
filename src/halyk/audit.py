"""Audit adjustment extraction and application.

Current audit documents contain several types of adjustments:
  * reclassifications — change category of a transaction
  * exclusions — remove a transaction from the covenant period
  * missing entries — a transaction not in the ledger extract
  * no-ops — explicitly confirmed no change needed

Only current-edition (final) audit docs are used; drafts are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .categorize import Category
from .ledger import LedgerEntry

CATEGORY_MAP: dict[str, Category] = {
    "операционные расходы": Category.OPEX,
    "консультационные услуги": Category.PROFESSIONAL,
    "процентные расходы": Category.INTEREST,
    "страховые премии": Category.INSURANCE,
    "коммунальные услуги": Category.UTILITIES,
    "арендные платежи": Category.LEASE,
    "расходы на оплату труда": Category.PERSONNEL,
    "капитальные затраты": Category.CAPEX,
    "налоги": Category.TAX,
    "маркетинговые расходы": Category.MARKETING,
}


class AdjustmentKind(StrEnum):
    RECLASSIFY = "reclassify"
    EXCLUDE = "exclude"
    MISSING_ENTRY = "missing_entry"
    NO_CHANGE = "no_change"


@dataclass
class AuditAdjustment:
    kind: AdjustmentKind
    txn_id: str | None
    amount: Decimal | None
    counterparty: str | None
    old_category: Category | None
    new_category: Category | None
    description: str


def _match_category(text: str) -> Category | None:
    lower = text.lower().strip()
    for phrase, cat in CATEGORY_MAP.items():
        if phrase in lower:
            return cat
    return None


RECLASS_AMOUNT = re.compile(
    r"[Сс]умма\s+в\s+размере\s+\$\s*([\d,]+(?:\.\d{2})?)"
    r".*?контрагент\w*\s+(.+?)"
    r",\s*первоначально\s+учт[её]нн\w+\s+как\s+(.+?)"
    r",\s*переклассифицирован\w*.*?как\s+(.+?)\.",
    re.S | re.I,
)

RECLASS_TXN = re.compile(
    r"[Оо]перация\s+(TXN-\w+-\d+)"
    r".*?первоначально\s+учт[её]нн\w+\s+как\s+(.+?)"
    r".*?переклассифицирован\w*.*?как\s+(.+?)\.",
    re.S | re.I,
)

EXCLUDE_TXN = re.compile(
    r"[Оо]перация\s+(TXN-\w+-\d+).*?исключен\w*\s+из\s+ковенантного\s+период",
    re.S | re.I,
)

CUTOFF_TXN = re.compile(
    r"[Оо]перация\s+(TXN-\w+-\d+).*?относится\s+к\s+услугам.*?период\s+с\s+(\d{4})",
    re.S | re.I,
)

MISSING_TXN = re.compile(
    r"[Оо]перация\s+(TXN-\w+-\d+).*?"
    r"(?:сумма\s+не\s+отражена|не\s+отражен\w*\s+в\s+выгрузке)"
    r".*?фактическ\w+\s+сумм\w+.*?\$\s*([\d,]+(?:\.\d{2})?)",
    re.S | re.I,
)

NO_CHANGE_TXN = re.compile(
    r"[Оо]перация\s+(TXN-\w+-\d+).*?первоначальная\s+классификация.*?сохраняется",
    re.S | re.I,
)

NO_RECLASS_NEEDED = re.compile(
    r"[Пп]ереклассификаций\s+за\s+ковенантный\s+период\s+не\s+требовалось",
    re.I,
)

FX_RATE = re.compile(
    r"([\d,]+(?:\.\d{2})?)\s*EUR"
    r".*?"
    r"\$\s*([\d,]+(?:\.\d{2})?)",
    re.S | re.I,
)


def extract_fx_rates(text: str) -> dict[str, Decimal]:
    """Extract EUR→USD rate from audit notes."""
    rates: dict[str, Decimal] = {}
    for m in FX_RATE.finditer(text):
        eur = Decimal(m.group(1).replace(",", ""))
        usd = Decimal(m.group(2).replace(",", ""))
        if eur > 0:
            rates["EUR"] = usd / eur
    return rates


#: Group-level capex is not in any ledger: it is derived from the PP&E note of
#: the ultimate parent's consolidated statements. With no disposals in the
#: year, additions = closing NBV - opening NBV + depreciation charge.
NBV_BEGIN = re.compile(
    r"Net book value at the beginning of the year\s*\n?\$\s*([\d,]+(?:\.\d{2})?)", re.I,
)
NBV_END = re.compile(
    r"Net book value at the end of the year\s*\n?\$\s*([\d,]+(?:\.\d{2})?)", re.I,
)
DEPRECIATION_CHARGE = re.compile(
    r"Depreciation charge for the year\s*\n?\$\s*([\d,]+(?:\.\d{2})?)", re.I,
)


def extract_group_capex(text: str) -> Decimal | None:
    """Capex additions from a consolidated PP&E movement note."""
    begin = NBV_BEGIN.search(text)
    end = NBV_END.search(text)
    dep = DEPRECIATION_CHARGE.search(text)
    if not (begin and end and dep):
        return None

    def _num(m: re.Match[str]) -> Decimal:
        return Decimal(m.group(1).replace(",", ""))

    return _num(end) - _num(begin) + _num(dep)


DISCLOSED_OBLIGATION = re.compile(
    r"(?:совокупное\s+)?обязательств\w+\s+по\s+программе\s+(.{3,80}?)"
    r"\s+в\s+размере\s+\$\s*([\d,]+(?:\.\d{2})?)"
    r".*?не\s+отражается\s+отдельной\s+операцией",
    re.S | re.I,
)


def extract_adjustments(audit_text: str) -> list[AuditAdjustment]:
    adjustments: list[AuditAdjustment] = []

    skip_reclass = bool(NO_RECLASS_NEEDED.search(audit_text))

    if not skip_reclass:
        for m in RECLASS_AMOUNT.finditer(audit_text):
            amount_str, counterparty, old_cat_text, new_cat_text = m.groups()
            adjustments.append(AuditAdjustment(
                kind=AdjustmentKind.RECLASSIFY,
                txn_id=None,
                amount=Decimal(amount_str.replace(",", "")),
                counterparty=counterparty.strip(),
                old_category=_match_category(old_cat_text),
                new_category=_match_category(new_cat_text),
                description=m.group(0)[:200],
            ))

        for m in RECLASS_TXN.finditer(audit_text):
            txn_id, old_cat_text, new_cat_text = m.groups()
            if NO_CHANGE_TXN.search(audit_text[m.start():m.end() + 200]):
                continue
            adjustments.append(AuditAdjustment(
                kind=AdjustmentKind.RECLASSIFY,
                txn_id=txn_id,
                amount=None,
                counterparty=None,
                old_category=_match_category(old_cat_text),
                new_category=_match_category(new_cat_text),
                description=m.group(0)[:200],
            ))

        for m in EXCLUDE_TXN.finditer(audit_text):
            adjustments.append(AuditAdjustment(
                kind=AdjustmentKind.EXCLUDE,
                txn_id=m.group(1),
                amount=None,
                counterparty=None,
                old_category=None,
                new_category=None,
                description=m.group(0)[:200],
            ))

        for m in CUTOFF_TXN.finditer(audit_text):
            txn_id = m.group(1)
            year = int(m.group(2))
            if year > 2025:
                adjustments.append(AuditAdjustment(
                    kind=AdjustmentKind.EXCLUDE,
                    txn_id=txn_id,
                    amount=None,
                    counterparty=None,
                    old_category=None,
                    new_category=None,
                    description=f"cutoff: services in {year}",
                ))

    for m in MISSING_TXN.finditer(audit_text):
        adjustments.append(AuditAdjustment(
            kind=AdjustmentKind.MISSING_ENTRY,
            txn_id=m.group(1),
            amount=Decimal(m.group(2).replace(",", "")),
            counterparty=None,
            old_category=None,
            new_category=None,
            description=m.group(0)[:200],
        ))

    for m in DISCLOSED_OBLIGATION.finditer(audit_text):
        desc_text = m.group(1).strip()
        amount = Decimal(m.group(2).replace(",", ""))
        cat = _match_category(desc_text)
        if cat is None and "пособ" in desc_text.lower():
            cat = Category.PERSONNEL
        adjustments.append(AuditAdjustment(
            kind=AdjustmentKind.MISSING_ENTRY,
            txn_id=None,
            amount=amount,
            counterparty=None,
            old_category=None,
            new_category=cat,
            description=f"disclosed obligation: {desc_text}",
        ))

    return adjustments


def _find_entry_by_amount(
    entries: list[LedgerEntry],
    amount: Decimal,
    counterparty: str | None,
) -> LedgerEntry | None:
    candidates = [
        e for e in entries
        if e.magnitude == amount
    ]
    if len(candidates) == 1:
        return candidates[0]
    if counterparty and candidates:
        cp_lower = counterparty.lower()
        for c in candidates:
            if cp_lower in c.counterparty.lower() or c.counterparty.lower() in cp_lower:
                return c
    return candidates[0] if candidates else None


def apply_adjustments(
    entries: list[LedgerEntry],
    adjustments: list[AuditAdjustment],
) -> list[LedgerEntry]:
    result = list(entries)

    for adj in adjustments:
        if adj.kind == AdjustmentKind.RECLASSIFY:
            target = None
            if adj.txn_id:
                target = next((e for e in result if e.txn_id == adj.txn_id), None)
            elif adj.amount is not None:
                target = _find_entry_by_amount(result, adj.amount, adj.counterparty)

            if target and adj.new_category:
                target.category = adj.new_category
                target.audit_reclassified = True

        elif adj.kind == AdjustmentKind.EXCLUDE:
            for e in result:
                if e.txn_id == adj.txn_id:
                    e.amount = None
                    e.defects.append("audit_excluded")

        elif adj.kind == AdjustmentKind.MISSING_ENTRY:
            if adj.txn_id and adj.amount is not None:
                existing = next((e for e in result if e.txn_id == adj.txn_id), None)
                if existing:
                    existing.amount = -adj.amount
                    existing.defects = [d for d in existing.defects if d != "missing_amount"]
            elif adj.txn_id is None and adj.amount is not None:
                from datetime import date as dt_date
                ref = result[0] if result else None
                synthetic = LedgerEntry(
                    txn_id=f"SYNTH-{adj.description[:20].replace(' ', '-')}",
                    scenario_id=ref.scenario_id if ref else "",
                    account_id=ref.account_id if ref else "",
                    day=dt_date(2025, 12, 31),
                    counterparty=adj.description,
                    description=adj.description,
                    amount=-adj.amount,
                    currency="USD",
                )
                if adj.new_category:
                    synthetic.category = adj.new_category
                result.append(synthetic)

    return result
