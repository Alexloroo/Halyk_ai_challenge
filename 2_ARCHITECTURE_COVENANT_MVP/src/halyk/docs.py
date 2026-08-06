"""The document archive.

Filenames are opaque hashes, so type and owner come from the text. Two traps
live here and both are decided in this module:

  * every scenario has a superseded 2024 edition of its agreement alongside the
    current one, carrying different thresholds and a period with no
    transactions in it;
  * audit notes come in a signed version and a draft interim statement.

Reading the wrong one is not a small error — it changes the threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import fitz


class DocKind(StrEnum):
    CREDIT_AGREEMENT = "credit_agreement"
    AUDIT_NOTES = "audit_notes"
    KYC = "kyc"
    COMPLIANCE = "compliance"
    OPERATIONS = "operations"
    UNKNOWN = "unknown"


class Edition(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    DRAFT = "draft"
    UNKNOWN = "unknown"


@dataclass
class Document:
    path: Path
    text: str
    kind: DocKind
    edition: Edition
    account_ids: list[str]
    pages: int

    @property
    def name(self) -> str:
        return self.path.name


SUPERSEDED = re.compile(r"НЕДЕЙСТВУЮЩАЯ\s+РЕДАКЦИЯ|Заменена и изложена", re.I)
DRAFT = re.compile(r"ПРОЕКТ\s*—\s*ПРОМЕЖУТОЧНАЯ|ПРОЕКТ —", re.I)
EXECUTION = re.compile(r"ИСПОЛНИТЕЛЬНЫЙ\s+ЭКЗЕМПЛЯР", re.I)
ACCOUNT = re.compile(r"ACC-\d{4}")

KIND_MARKERS: list[tuple[re.Pattern[str], DocKind]] = [
    (re.compile(r"Финансовые ковенанты|Статья 6|ДОГОВОР БАНКОВСКОГО", re.I),
     DocKind.CREDIT_AGREEMENT),
    (re.compile(r"Независимый аудитор|Registered Auditors|Statutory Auditors|"
                r"ПРИМЕЧАНИЯ К ФИНАНСОВОЙ", re.I), DocKind.AUDIT_NOTES),
    (re.compile(r"Знай своего клиент|финансового мониторинга|KYC-ACC", re.I), DocKind.KYC),
    (re.compile(r"Комплаенс\s*—|Контролируемый документ", re.I), DocKind.COMPLIANCE),
    (re.compile(r"Проект «Атлас»|Еженедельное обновление", re.I), DocKind.OPERATIONS),
]


def _classify(text: str) -> tuple[DocKind, Edition]:
    head = text[:400]
    if SUPERSEDED.search(head):
        edition = Edition.SUPERSEDED
    elif DRAFT.search(head):
        edition = Edition.DRAFT
    elif EXECUTION.search(head):
        edition = Edition.CURRENT
    else:
        edition = Edition.CURRENT

    for pattern, kind in KIND_MARKERS:
        if pattern.search(text):
            return kind, edition
    return DocKind.UNKNOWN, edition


def load_documents(directory: Path) -> list[Document]:
    """Read every PDF. Non-PDF files in the folder are skipped, not an error."""
    documents: list[Document] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() != ".pdf":
            continue
        try:
            with fitz.open(path) as pdf:
                text = "\n".join(page.get_text() for page in pdf)
                pages = len(pdf)
        except Exception:
            continue
        kind, edition = _classify(text)
        documents.append(
            Document(
                path=path,
                text=text,
                kind=kind,
                edition=edition,
                account_ids=sorted(set(ACCOUNT.findall(text))),
                pages=pages,
            )
        )
    return documents


def pick(
    documents: list[Document],
    kind: DocKind,
    account_id: str,
    *,
    edition: Edition = Edition.CURRENT,
) -> Document | None:
    """The one document of this kind, for this account, in this edition."""
    matches = [
        d for d in documents
        if d.kind is kind and d.edition is edition and account_id in d.account_ids
    ]
    if not matches:
        return None
    # Prefer the longest: an agreement beats a one-page mention of the same account.
    return max(matches, key=lambda d: len(d.text))
