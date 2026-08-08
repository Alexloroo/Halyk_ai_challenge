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

import os
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import repeat
from pathlib import Path

import pymupdf


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
    native_pages: list[int] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    ocr_failed_pages: list[int] = field(default_factory=list)
    ocr_language: str = ""
    ocr_dpi: int | None = None

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class DocumentLoadIssue:
    """A PDF read or page OCR failure retained for diagnostics."""

    path: Path
    error_type: str
    message: str
    operation: str = "pdf_read"
    page: int | None = None


@dataclass(frozen=True)
class OCRConfig:
    """Selective Tesseract OCR settings for pages without native text."""

    enabled: bool = True
    language: str = "rus+kaz+eng"
    dpi: int = 300
    min_native_chars: int = 20

    @classmethod
    def from_environment(cls) -> OCRConfig:
        enabled = os.getenv("HALYK_OCR_ENABLED", "1").strip().casefold()
        return cls(
            enabled=enabled not in {"0", "false", "no", "off"},
            language=os.getenv("HALYK_OCR_LANGUAGE", "rus+kaz+eng"),
            dpi=int(os.getenv("HALYK_OCR_DPI", "300")),
            min_native_chars=int(os.getenv("HALYK_OCR_MIN_NATIVE_CHARS", "20")),
        )


SUPERSEDED = re.compile(
    r"НЕДЕЙСТВУЮЩАЯ\s+РЕДАКЦИЯ|Заменена и изложена|"
    r"КҮШІН\s+ЖОЙҒАН\s+РЕДАКЦИЯ|КҮШІ\s+ЖОЙЫЛҒАН\s+НҰСҚА",
    re.I,
)
DRAFT = re.compile(r"ПРОЕКТ\s*—\s*ПРОМЕЖУТОЧНАЯ|ПРОЕКТ —|ЖОБА\s*[—-]", re.I)
EXECUTION = re.compile(r"ИСПОЛНИТЕЛЬНЫЙ\s+ЭКЗЕМПЛЯР|ОРЫНДАУ\s+ДАНАСЫ", re.I)
NON_BINDING = re.compile(
    r"МЕТОДИЧЕСК\w+\s+МЕМОРАНДУМ|ВНУТРЕНН\w+\s+УЧЕБН|"
    r"не\s+является\s+кредитным\s+договором|не\s+созда[её]т\s+обязательств|"
    r"исключительно\s+для\s+обучения|training\s+(?:memo|example)|"
    r"not\s+(?:a\s+)?(?:credit|loan|facility)\s+agreement|"
    r"creates?\s+no\s+(?:legal\s+)?obligations|informational\s+only",
    re.I,
)
AGREEMENT_AUTHORITY = re.compile(
    r"ДОГОВОР\s+БАНКОВСКОГО\s+ЗАЙМА|КРЕДИТН\w+\s+ДОГОВОР|"
    r"ИСПОЛНИТЕЛЬНЫЙ\s+ЭКЗЕМПЛЯР|БАНКТІК\s+ҚАРЫЗ\s+ШАРТЫ|"
    r"КРЕДИТТІК\s+ШАРТ|ОРЫНДАУ\s+ДАНАСЫ",
    re.I,
)
ACCOUNT = re.compile(r"ACC-\d{4,}(?![-\d])")
OCR_ACCOUNT = re.compile(r"[AА][CС][CС]\s*[-‐‑‒–—]\s*(\d{4,})(?![-\d])", re.I)

KIND_MARKERS: list[tuple[re.Pattern[str], DocKind]] = [
    (
        re.compile(
            r"Финансовые ковенанты|Статья 6|ДОГОВОР БАНКОВСКОГО|"
            r"Қаржылық ковенанттар|6\s*[-–—]?\s*бап|БАНКТІК ҚАРЫЗ ШАРТЫ",
            re.I,
        ),
        DocKind.CREDIT_AGREEMENT,
    ),
    (
        re.compile(
            r"Независимый аудитор|Registered Auditors|Statutory Auditors|"
            r"ПРИМЕЧАНИЯ К ФИНАНСОВОЙ|Тәуелсіз аудитор|"
            r"ҚАРЖЫЛЫҚ ЕСЕПТІЛІККЕ ЕСКЕРТПЕЛЕР",
            re.I,
        ),
        DocKind.AUDIT_NOTES,
    ),
    (
        re.compile(
            r"Знай своего клиент|финансового мониторинга|KYC-ACC|"
            r"Клиентті таны|қаржылық мониторинг",
            re.I,
        ),
        DocKind.KYC,
    ),
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


def _ocr_page(page: pymupdf.Page, config: OCRConfig) -> str:
    textpage = page.get_textpage_ocr(
        language=config.language,
        dpi=config.dpi,
        full=True,
    )
    return page.get_text(textpage=textpage)


def _normalize_ocr_text(text: str) -> str:
    """Normalize only stable identifiers affected by Cyrillic/Latin OCR ambiguity."""
    return OCR_ACCOUNT.sub(lambda match: f"ACC-{match.group(1)}", text)


def _pdf_worker_count() -> int:
    default = min(4, os.cpu_count() or 1)
    try:
        configured = int(os.getenv("HALYK_PDF_WORKERS", str(default)))
    except ValueError:
        return default
    return configured if configured > 0 else default


def _load_document(
    path: Path, config: OCRConfig
) -> tuple[Document | None, list[DocumentLoadIssue]]:
    """Load one PDF independently, including optional page-level OCR."""
    document_issues: list[DocumentLoadIssue] = []
    try:
        with pymupdf.open(path) as pdf:
            page_texts: list[str] = []
            native_pages: list[int] = []
            ocr_pages: list[int] = []
            ocr_failed_pages: list[int] = []
            for page_number, page in enumerate(pdf, start=1):
                native_text = page.get_text()
                if len(native_text.strip()) >= config.min_native_chars:
                    native_pages.append(page_number)
                    page_texts.append(native_text)
                    continue
                if not config.enabled:
                    if native_text.strip():
                        native_pages.append(page_number)
                    page_texts.append(native_text)
                    continue
                try:
                    page_texts.append(_normalize_ocr_text(_ocr_page(page, config)))
                    ocr_pages.append(page_number)
                except Exception as exc:
                    page_texts.append(native_text)
                    ocr_failed_pages.append(page_number)
                    document_issues.append(
                        DocumentLoadIssue(
                            path=path,
                            error_type=type(exc).__name__,
                            message=str(exc),
                            operation="ocr",
                            page=page_number,
                        )
                    )
            text = "\n".join(page_texts)
            pages = len(pdf)
    except Exception as exc:
        document_issues.append(
            DocumentLoadIssue(
                path=path,
                error_type=type(exc).__name__,
                message=str(exc),
            )
        )
        return None, document_issues

    kind, edition = _classify(text)
    return (
        Document(
            path=path,
            text=text,
            kind=kind,
            edition=edition,
            account_ids=sorted(set(ACCOUNT.findall(text))),
            pages=pages,
            native_pages=native_pages,
            ocr_pages=ocr_pages,
            ocr_failed_pages=ocr_failed_pages,
            ocr_language=config.language if ocr_pages or ocr_failed_pages else "",
            ocr_dpi=config.dpi if ocr_pages or ocr_failed_pages else None,
        ),
        document_issues,
    )


def load_documents(
    directory: Path,
    *,
    issues: list[DocumentLoadIssue] | None = None,
    ocr_config: OCRConfig | None = None,
) -> list[Document]:
    """Read every PDF. Non-PDF files in the folder are skipped, not an error."""
    config = ocr_config or OCRConfig.from_environment()
    paths = sorted(path for path in directory.iterdir() if path.suffix.lower() == ".pdf")
    if not paths:
        return []

    workers = _pdf_worker_count()
    if workers == 1:
        loaded = [_load_document(path, config) for path in paths]
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(paths))) as executor:
            loaded = list(executor.map(_load_document, paths, repeat(config)))

    documents: list[Document] = []
    for document, document_issues in loaded:
        if issues is not None:
            issues.extend(document_issues)
        if document is not None:
            documents.append(document)
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
        d
        for d in documents
        if d.kind is kind and d.edition is edition and account_id in d.account_ids
    ]
    if not matches:
        return None

    def authority(document: Document) -> tuple[int, int, int]:
        """Rank legal authority before completeness/length.

        A long training memo can quote every covenant while explicitly being
        non-binding.  Length is therefore only a final tie-breaker.
        """
        negative = bool(NON_BINDING.search(document.text))
        positive = (
            bool(AGREEMENT_AUTHORITY.search(document.text))
            if kind is DocKind.CREDIT_AGREEMENT
            else True
        )
        return (0 if negative else 1, 1 if positive else 0, len(document.text))

    return max(matches, key=authority)
