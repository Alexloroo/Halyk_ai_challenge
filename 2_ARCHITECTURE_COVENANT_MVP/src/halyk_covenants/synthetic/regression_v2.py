from __future__ import annotations

import csv
import json
import shutil
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import fitz
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantSpec,
    EvidenceMode,
    FilterSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
)
from halyk_covenants.synthetic.fonts import FontFamily, register_cyrillic_fonts

_DATASET_ID = "halyk-synthetic-covenants-v2"


def generate_regression_dataset_v2(output_dir: Path) -> dict[str, Any]:
    """Generate an end-to-end regression corpus for the clarified challenge format.

    Gold files are intentionally separated from ``input`` so a solver can be pointed only at the
    public-like input tree. The local JSON shape and ratio convention are synthetic and are not
    claimed to match Halyk's final official template.
    """
    output_dir = output_dir.resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    input_dir = output_dir / "input"
    documents_dir = input_dir / "documents"
    gold_dir = output_dir / "gold"
    covenant_dir = gold_dir / "covenants"
    documents_dir.mkdir(parents=True)
    covenant_dir.mkdir(parents=True)

    fonts = register_cyrillic_fonts()
    _write_borrowers(input_dir / "borrowers.csv")
    _write_transactions(input_dir / "transactions.csv")
    _write_context(input_dir / "evaluation_context.json")
    _render_alpha_contract(documents_dir / "alpha_loan_agreement.pdf", fonts)
    _render_alpha_amendment(documents_dir / "alpha_amendment.pdf", fonts)
    _render_beta_scan(documents_dir / "beta_covenants_scan.pdf", fonts)
    _render_portfolio_table(documents_dir / "portfolio_covenants_table.pdf", fonts)

    specs = _gold_covenants()
    for spec in specs:
        _write_json(covenant_dir / f"{spec.covenant_id}.json", spec.model_dump(mode="json"))

    _write_json(input_dir / "submission_template.json", _submission_template())
    _write_json(gold_dir / "expected_submission.json", _expected_submission())
    (gold_dir / "calculations.md").write_text(_gold_calculations(), encoding="utf-8")

    manifest = {
        "dataset_id": _DATASET_ID,
        "borrowers": 4,
        "covenants": 9,
        "transactions": 15,
        "documents": 4,
        "features": [
            "native_pdf",
            "image_only_pdf",
            "multi_borrower_table",
            "mid_period_amendment",
            "sum",
            "count",
            "max",
            "ratio",
            "existence",
            "weekday_filter",
            "partial_credit_components",
        ],
    }
    _write_json(output_dir / "dataset_manifest.json", manifest)
    return manifest


def _write_borrowers(path: Path) -> None:
    rows = [
        ("B001", "Alpha Trade", "990140000001"),
        ("B002", "Beta Logistics", "990140000002"),
        ("B003", "Gamma Retail", "990140000003"),
        ("B004", "Delta Construction", "990140000004"),
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["borrower_id", "canonical_name", "bin"])
        writer.writerows(rows)


def _write_transactions(path: Path) -> None:
    rows = [
        ("TX-A1", "B001", "2026-04-01", "5000000", "KZT", "outgoing", "CP-001", "Vendor One LLP", "Оплата по договору A-11"),
        ("TX-A2", "B001", "2026-04-10", "6000000", "KZT", "outgoing", "CP-002", "Vendor Two LLP", "Оплата оборудования"),
        ("TX-A3", "B001", "2026-04-20", "5000000", "KZT", "outgoing", "CP-003", "Vendor Three LLP", "Оплата услуг"),
        ("TX-A4", "B001", "2026-04-22", "2000000", "KZT", "incoming", "CP-010", "Customer X", "Поступление выручки"),
        ("TX-B1", "B002", "2026-04-05", "2500000", "KZT", "outgoing", "CP-RF", "RED FLAG LLP", "Оплата по счёту RF-77"),
        ("TX-B2", "B002", "2026-04-03", "4000000", "KZT", "incoming", "CP-021", "Client A", "Оплата логистических услуг"),
        ("TX-B3", "B002", "2026-04-18", "5000000", "KZT", "incoming", "CP-022", "Client B", "Оплата перевозки"),
        ("TX-B4", "B002", "2026-04-25", "3500000", "KZT", "outgoing", "CP-023", "Fuel Partner", "Топливо"),
        ("TX-G1", "B003", "2026-04-04", "4000000", "KZT", "outgoing", "CP-GA", "Vendor A", "Закуп товара"),
        ("TX-G2", "B003", "2026-04-07", "3000000", "KZT", "outgoing", "CP-GA", "Vendor A", "Закуп товара"),
        ("TX-G3", "B003", "2026-04-14", "2000000", "KZT", "outgoing", "CP-GB", "Vendor B", "Логистика"),
        ("TX-G4", "B003", "2026-04-21", "1000000", "KZT", "outgoing", "CP-GC", "Vendor C", "Маркетинг"),
        ("TX-D1", "B004", "2026-04-02", "7000000", "KZT", "outgoing", "CP-D1", "Concrete LLP", "Материалы"),
        ("TX-D2", "B004", "2026-04-16", "8000000", "KZT", "outgoing", "CP-D2", "Steel LLP", "Металлопрокат"),
        ("TX-D3", "B004", "2026-04-28", "3000000", "KZT", "outgoing", "CP-D3", "Transport LLP", "Доставка"),
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "transaction_id",
                "borrower_id",
                "transaction_date",
                "amount",
                "currency",
                "direction",
                "counterparty_id",
                "counterparty_name",
                "purpose",
            ]
        )
        writer.writerows(rows)


def _write_context(path: Path) -> None:
    _write_json(
        path,
        {
            "dataset_id": _DATASET_ID,
            "monitoring_period": {"start": "2026-04-01", "end": "2026-04-30"},
            "number_conventions": {
                "money": "absolute KZT number",
                "count": "integer",
                "ratio": "decimal from 0 to 1",
            },
            "warning": "Synthetic conventions only; replace serializer rules with official template.",
        },
    )


def _render_alpha_contract(path: Path, fonts: FontFamily) -> None:
    sections = [
        ("Заёмщик", "Alpha Trade | Borrower ID: B001 | БИН: 990140000001"),
        ("COV-A1", "Совокупный объём исходящих платежей в KZT за календарный месяц не должен превышать 15 000 000 KZT."),
        ("COV-A2", "Каждая отдельная исходящая транзакция в KZT не должна превышать 5 500 000 KZT. При нарушении требуется конкретная транзакция-улика. Условие действует с 01.01.2026 до изменения дополнительным соглашением."),
        ("COV-A3", "Количество исходящих KZT-транзакций на сумму строго более 1 000 000 KZT за календарный месяц не должно превышать 3."),
    ]
    _render_text_pdf(path, "Кредитное соглашение — Alpha Trade", sections, fonts)


def _render_alpha_amendment(path: Path, fonts: FontFamily) -> None:
    _render_text_pdf(
        path,
        "Дополнительное соглашение №1 — Alpha Trade",
        [
            ("Ссылка", "Borrower ID: B001. Изменяет COV-A2."),
            ("Изменение", "Начиная с 15.04.2026 максимальный размер одной отдельной исходящей KZT-транзакции увеличивается с 5 500 000 KZT до 6 500 000 KZT."),
        ],
        fonts,
    )


def _render_beta_scan(path: Path, fonts: FontFamily) -> None:
    with tempfile.TemporaryDirectory(prefix="halyk-scan-") as temp_dir:
        native = Path(temp_dir) / "native.pdf"
        _render_text_pdf(
            native,
            "Кредитный мониторинг — Beta Logistics",
            [
                ("Заёмщик", "Beta Logistics | Borrower ID: B002"),
                ("COV-B1", "Заёмщик не должен осуществлять исходящие платежи в пользу контрагента RED FLAG LLP. Число — количество таких транзакций; при нарушении указывается транзакция-улика."),
                ("COV-B2", "Совокупный объём входящих платежей в KZT за календарный месяц должен быть не менее 8 000 000 KZT."),
            ],
            fonts,
        )
        source = fitz.open(native)
        target = fitz.open()
        try:
            for source_page in source:
                pixmap = source_page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                png = pixmap.tobytes("png")
                page = target.new_page(width=source_page.rect.width, height=source_page.rect.height)
                page.insert_image(page.rect, stream=png)
            target.save(path)
        finally:
            target.close()
            source.close()


def _render_portfolio_table(path: Path, fonts: FontFamily) -> None:
    page = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    width, height = A4
    x = 14 * mm
    y = height - 18 * mm
    page.setFont(fonts.bold, 15)
    page.drawString(x, y, "Реестр ковенантов портфеля")
    y -= 12 * mm
    rows = [
        ("Gamma Retail", "B003", "COV-G1", "Максимальная доля исходящих KZT-платежей одному контрагенту за месяц <= 40% общего исходящего KZT-оборота."),
        ("Gamma Retail", "B003", "COV-G2", "Исходящая KZT-транзакция в субботу или воскресенье не должна превышать 2 000 000 KZT."),
        ("Delta Construction", "B004", "COV-D1", "Совокупный объём исходящих KZT-платежей за месяц <= 20 000 000 KZT."),
        ("Delta Construction", "B004", "COV-D2", "Количество исходящих KZT-транзакций свыше 5 000 000 KZT за месяц <= 2."),
    ]
    col_widths = [35 * mm, 18 * mm, 23 * mm, 106 * mm]
    headers = ["Заёмщик", "ID", "Ковенант", "Условие"]
    row_height = 29 * mm
    header_height = 11 * mm
    page.setFillColor(colors.HexColor("#E9EEF5"))
    page.rect(x, y - header_height, sum(col_widths), header_height, fill=1, stroke=1)
    page.setFillColor(colors.black)
    cursor = x
    page.setFont(fonts.bold, 8)
    for index, header in enumerate(headers):
        page.drawString(cursor + 2 * mm, y - 7 * mm, header)
        cursor += col_widths[index]
    y -= header_height
    page.setFont(fonts.regular, 7.5)
    for row in rows:
        cursor = x
        for index, value in enumerate(row):
            page.rect(cursor, y - row_height, col_widths[index], row_height, fill=0, stroke=1)
            lines = _wrap(value, [18, 8, 10, 60][index])
            text_y = y - 5 * mm
            for line in lines[:6]:
                page.drawString(cursor + 2 * mm, text_y, line)
                text_y -= 4.2 * mm
            cursor += col_widths[index]
        y -= row_height
    page.setFont(fonts.regular, 7)
    page.drawString(x, 12 * mm, "Синтетический документ; ratio в gold-файлах хранится как 0..1.")
    page.save()


def _render_text_pdf(
    path: Path,
    title: str,
    sections: list[tuple[str, str]],
    fonts: FontFamily,
) -> None:
    page = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    width, height = A4
    x = 20 * mm
    y = height - 20 * mm
    page.setFont(fonts.bold, 15)
    page.drawString(x, y, title)
    y -= 13 * mm
    for heading, body in sections:
        page.setFont(fonts.bold, 10.5)
        page.drawString(x, y, heading)
        y -= 6 * mm
        page.setFont(fonts.regular, 9.5)
        for line in _wrap(body, 92):
            page.drawString(x, y, line)
            y -= 5 * mm
        y -= 4 * mm
    page.setFont(fonts.regular, 7)
    page.drawString(x, 11 * mm, "Синтетический документ. Не содержит реальных банковских данных.")
    page.save()


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if len(candidate) > width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _base_filters(direction: str) -> list[FilterSpec]:
    return [
        FilterSpec(field="direction", operator="eq", value=direction),
        FilterSpec(field="currency", operator="eq", value="KZT"),
    ]


def _spec(
    covenant_id: str,
    borrower_id: str,
    metric: MetricSpec,
    comparator: str,
    threshold: Decimal | int,
    *,
    filters: list[FilterSpec],
    source_document: str,
    evidence_mode: EvidenceMode = EvidenceMode.NONE,
    group_by: list[str] | None = None,
    covenant_group_id: str | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    unit: str | None = None,
) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        covenant_group_id=covenant_group_id,
        raw_text=f"Synthetic compiled rule {covenant_id}",
        borrower_ids=[borrower_id],
        metric=metric,
        condition=ConditionSpec(
            comparator=comparator,  # type: ignore[arg-type]
            threshold=threshold,
            unit=unit,
            currency="KZT" if unit == "KZT" else None,
        ),
        transaction_filters=filters,
        group_by=group_by or [],
        time_window=TimeWindowSpec(type="calendar_month"),
        evidence_mode=evidence_mode,
        effective_from=effective_from,
        effective_to=effective_to,
        source=SourceRef(document_id=source_document, page=1),
        confidence=1,
    )


def _gold_covenants() -> list[CovenantSpec]:
    outgoing = _base_filters("outgoing")
    incoming = _base_filters("incoming")
    specs = [
        _spec("COV-A1", "B001", MetricSpec(metric_type="sum", field="amount", unit="KZT"), "<=", Decimal("15000000"), filters=outgoing, source_document="alpha_loan_agreement.pdf", unit="KZT"),
        _spec("COV-A2-v1", "B001", MetricSpec(metric_type="max", field="amount", unit="KZT"), "<=", Decimal("5500000"), filters=outgoing, source_document="alpha_loan_agreement.pdf", evidence_mode=EvidenceMode.VIOLATING_TRANSACTION, covenant_group_id="COV-A2", effective_from=date(2026, 1, 1), effective_to=date(2026, 4, 14), unit="KZT"),
        _spec("COV-A2-v2", "B001", MetricSpec(metric_type="max", field="amount", unit="KZT"), "<=", Decimal("6500000"), filters=outgoing, source_document="alpha_amendment.pdf", evidence_mode=EvidenceMode.VIOLATING_TRANSACTION, covenant_group_id="COV-A2", effective_from=date(2026, 4, 15), unit="KZT"),
        _spec("COV-A3", "B001", MetricSpec(metric_type="count", field="transaction_id", unit="count"), "<=", 3, filters=[*outgoing, FilterSpec(field="amount", operator="gt", value=1000000)], source_document="alpha_loan_agreement.pdf", unit="count"),
        _spec("COV-B1", "B002", MetricSpec(metric_type="existence", field="transaction_id", unit="count"), "==", 0, filters=[*outgoing, FilterSpec(field="counterparty_name", operator="eq", value="RED FLAG LLP")], source_document="beta_covenants_scan.pdf", evidence_mode=EvidenceMode.VIOLATING_TRANSACTION, unit="count"),
        _spec("COV-B2", "B002", MetricSpec(metric_type="sum", field="amount", unit="KZT"), ">=", Decimal("8000000"), filters=incoming, source_document="beta_covenants_scan.pdf", unit="KZT"),
        _spec("COV-G1", "B003", MetricSpec(metric_type="ratio", numerator=MetricSpec(metric_type="sum", field="amount"), denominator=MetricSpec(metric_type="sum", field="amount"), unit="ratio"), "<=", Decimal("0.4"), filters=outgoing, source_document="portfolio_covenants_table.pdf", group_by=["counterparty_id"], unit="ratio"),
        _spec("COV-G2", "B003", MetricSpec(metric_type="max", field="amount", unit="KZT"), "<=", Decimal("2000000"), filters=[*outgoing, FilterSpec(field="weekday", operator="in", value=[6, 7])], source_document="portfolio_covenants_table.pdf", evidence_mode=EvidenceMode.VIOLATING_TRANSACTION, unit="KZT"),
        _spec("COV-D1", "B004", MetricSpec(metric_type="sum", field="amount", unit="KZT"), "<=", Decimal("20000000"), filters=outgoing, source_document="portfolio_covenants_table.pdf", unit="KZT"),
        _spec("COV-D2", "B004", MetricSpec(metric_type="count", field="transaction_id", unit="count"), "<=", 2, filters=[*outgoing, FilterSpec(field="amount", operator="gt", value=5000000)], source_document="portfolio_covenants_table.pdf", unit="count"),
    ]
    return specs


def _submission_template() -> dict[str, Any]:
    covenant_ids = {
        "B001": ["COV-A1", "COV-A2", "COV-A3"],
        "B002": ["COV-B1", "COV-B2"],
        "B003": ["COV-G1", "COV-G2"],
        "B004": ["COV-D1", "COV-D2"],
    }
    return {
        "dataset_id": _DATASET_ID,
        "results": [
            {
                "borrower_id": borrower_id,
                "covenants": [
                    {
                        "covenant_id": covenant_id,
                        "verdict": None,
                        "number": None,
                        "evidence_transaction_id": None,
                    }
                    for covenant_id in ids
                ],
            }
            for borrower_id, ids in covenant_ids.items()
        ],
    }


def _expected_submission() -> dict[str, Any]:
    return {
        "dataset_id": _DATASET_ID,
        "results": [
            {"borrower_id": "B001", "covenants": [
                {"covenant_id": "COV-A1", "verdict": "violated", "number": 16000000, "evidence_transaction_id": None},
                {"covenant_id": "COV-A2", "verdict": "violated", "number": 6000000, "evidence_transaction_id": "TX-A2"},
                {"covenant_id": "COV-A3", "verdict": "complied", "number": 3, "evidence_transaction_id": None},
            ]},
            {"borrower_id": "B002", "covenants": [
                {"covenant_id": "COV-B1", "verdict": "violated", "number": 1, "evidence_transaction_id": "TX-B1"},
                {"covenant_id": "COV-B2", "verdict": "complied", "number": 9000000, "evidence_transaction_id": None},
            ]},
            {"borrower_id": "B003", "covenants": [
                {"covenant_id": "COV-G1", "verdict": "violated", "number": 0.7, "evidence_transaction_id": None},
                {"covenant_id": "COV-G2", "verdict": "violated", "number": 4000000, "evidence_transaction_id": "TX-G1"},
            ]},
            {"borrower_id": "B004", "covenants": [
                {"covenant_id": "COV-D1", "verdict": "complied", "number": 18000000, "evidence_transaction_id": None},
                {"covenant_id": "COV-D2", "verdict": "complied", "number": 2, "evidence_transaction_id": None},
            ]},
        ],
    }


def _gold_calculations() -> str:
    return """# Synthetic v2 gold calculations

- B001/COV-A1: 5M + 6M + 5M = 16M > 15M -> violated; aggregate evidence is null.
- B001/COV-A2: TX-A2 on 2026-04-10 is 6M against the then-active 5.5M limit -> violated; amendment raises the limit only from 2026-04-15.
- B001/COV-A3: three outgoing KZT transactions are strictly above 1M -> 3 <= 3.
- B002/COV-B1: RED FLAG LLP appears once -> violated, TX-B1.
- B002/COV-B2: 4M + 5M incoming = 9M >= 8M.
- B003/COV-G1: Vendor A receives 7M / total 10M = 0.7 > 0.4.
- B003/COV-G2: 2026-04-04 is Saturday; TX-G1 = 4M > 2M.
- B004/COV-D1: 7M + 8M + 3M = 18M <= 20M.
- B004/COV-D2: exactly two transactions are >5M -> boundary complied.
"""


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
