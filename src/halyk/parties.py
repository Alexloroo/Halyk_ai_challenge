"""Related parties, from the KYC dossier.

The dossier lists counterparties with their voting stake and then states the
threshold in prose — "20.0% and above are treated as related parties for the
purposes of the Agreement". Both halves matter: for ACC-7801 the table holds
34.5%, 18.7% and 6.2%, and only the first qualifies. Taking every row from the
table would triple the related-party total.

The threshold is read from the document. It is never defaulted: assuming 20%
because one dossier said so is exactly the kind of guess this rewrite exists to
remove.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .ledger import LedgerEntry

THRESHOLD = re.compile(
    r"владеет\s+(\d+(?:[.,]\d+)?)\s*%\s*и\s*более|"
    r"(\d+(?:[.,]\d+)?)\s*%\s*и\s*более|"
    r"(?:иеленетін\s+үлесі\s+)?(\d+(?:[.,]\d+)?)\s*%\s*(?:және\s+одан\s+жоғары|"
    r"немесе\s+одан\s+көп)",
    re.I,
)
#: Some dossiers carry a second table — the share of each subsidiary's assets
#: pledged as security. Subsidiaries below the stated pledge threshold sit
#: outside the security perimeter and count as *unrestricted* for the
#: agreement. That table must not leak into the ownership parsing.
PLEDGE_SECTION = re.compile(
    r"(?:Обеспечительное\s+покрытие\s+дочерних\s+организаций.*?как\s+неограниченные|"
    r"Еншілес\s+ұйымдардың\s+қамтамасыз\s+ету\s+қамтылуы.*?шектелмеген\s+ұйымдар)\.",
    re.S | re.I,
)
PLEDGE_THRESHOLD = re.compile(
    r"ниже\s+(\d+(?:[.,]\d+)?)\s*%|"
    r"(\d+(?:[.,]\d+)?)\s*%\s*(?:-дан|-ден|-тан|-тен)\s+төмен",
    re.I,
)
#: The dossier renders its ownership table one cell per line:
#:     Aktau Holdings LLP
#:     34.5%
#: so the name and its share are on separate lines, not side by side.
HOLDING = re.compile(
    r"^[ \t]*([^\n%]*?(?:L\.?L\.?P\.?|JSC|LLC|Ltd|АО|ТОО|ООО|ЖШС|АҚ))\.?[ \t]*\n"
    r"[ \t]*(\d+(?:[.,]\d+)?)[ \t]*%",
    re.MULTILINE,
)


@dataclass
class RelatedParties:
    scenario_id: str
    threshold_percent: Decimal | None
    holdings: list[tuple[str, Decimal]] = field(default_factory=list)
    names: frozenset[str] = frozenset()
    unrestricted: frozenset[str] = frozenset()

    @property
    def resolved(self) -> bool:
        return self.threshold_percent is not None


def _number(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def extract_related_parties(scenario_id: str, kyc_text: str) -> RelatedParties:
    unrestricted: frozenset[str] = frozenset()
    pledge = PLEDGE_SECTION.search(kyc_text)
    if pledge:
        section = pledge.group(0)
        kyc_text = kyc_text[: pledge.start()] + kyc_text[pledge.end():]
        pledge_thr = PLEDGE_THRESHOLD.search(section)
        if pledge_thr:
            limit = _number(pledge_thr.group(1) or pledge_thr.group(2))
            unrestricted = frozenset(
                " ".join(name.split())
                for name, share in HOLDING.findall(section)
                if _number(share) < limit
            )

    match = THRESHOLD.search(kyc_text)
    threshold = _number(next(group for group in match.groups() if group)) if match else None

    holdings = [
        (" ".join(name.split()), _number(share))
        for name, share in HOLDING.findall(kyc_text)
    ]
    names = (
        frozenset(name for name, share in holdings if share >= threshold)
        if threshold is not None
        else frozenset()
    )
    return RelatedParties(
        scenario_id=scenario_id,
        threshold_percent=threshold,
        holdings=holdings,
        names=names,
        unrestricted=unrestricted,
    )


def _key(name: str) -> str:
    """Canonical legal name used for exact matching.

    Office/location annotations and legal suffixes are presentation details;
    substantive extra words (for example ``Advisory``) are not aliases.
    """
    name = re.sub(r"\([^)]*\)", "", name)
    text = re.sub(
        r"\b(L\.?L\.?P\.?|JSC|LLC|Ltd|АО|ТОО|ООО|ЖШС|АҚ)\b", "", name, flags=re.I
    )
    return " ".join(re.sub(r"[^a-zа-яё0-9 ]", " ", text.casefold()).split())


def mark_related(entries: list[LedgerEntry], parties: RelatedParties) -> int:
    """Flag entries paid to a related party. Returns how many were flagged."""
    if not parties.names:
        return 0
    keys = {_key(name) for name in parties.names}
    flagged = 0
    for entry in entries:
        counterparty = _key(entry.counterparty)
        if counterparty and counterparty in keys:
            entry.is_related_party = True
            flagged += 1
    return flagged


def mark_unrestricted(entries: list[LedgerEntry], parties: RelatedParties) -> int:
    """Flag entries paid to an unrestricted subsidiary."""
    if not parties.unrestricted:
        return 0
    keys = {_key(name) for name in parties.unrestricted}
    flagged = 0
    for entry in entries:
        counterparty = _key(entry.counterparty)
        if counterparty and counterparty in keys:
            entry.is_unrestricted_transfer = True
            flagged += 1
    return flagged
