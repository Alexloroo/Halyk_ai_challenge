from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from halyk_covenants.synthetic.fonts import FontFamily, register_cyrillic_fonts
from halyk_covenants.synthetic.models import SyntheticDatasetDefinition


class DeterministicCanvas(canvas.Canvas):
    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs["invariant"] = 1
        super().__init__(*args, **kwargs)


def render_pdfs(definition: SyntheticDatasetDefinition, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    fonts = register_cyrillic_fonts()
    alpha_path = directory / "alpha_trade_contract.pdf"
    appendix_path = directory / "borrower_limits_appendix.pdf"
    _render_alpha_contract(alpha_path, fonts)
    _render_limits_appendix(appendix_path, fonts)
    return [alpha_path, appendix_path]


def _styles(fonts: FontFamily) -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "Title",
            fontName=fonts.bold,
            fontSize=16,
            leading=20,
            alignment=TA_CENTER,
            spaceAfter=8 * mm,
        ),
        "heading": ParagraphStyle(
            "Heading",
            fontName=fonts.bold,
            fontSize=11,
            leading=14,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "Body",
            fontName=fonts.regular,
            fontSize=9.5,
            leading=13,
            alignment=TA_LEFT,
            spaceAfter=2.5 * mm,
        ),
        "small": ParagraphStyle(
            "Small",
            fontName=fonts.regular,
            fontSize=7.5,
            leading=9.5,
            spaceAfter=1.5 * mm,
        ),
        "table": ParagraphStyle(
            "Table",
            fontName=fonts.regular,
            fontSize=7.5,
            leading=9,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            fontName=fonts.bold,
            textColor=colors.white,
            fontSize=7.5,
            leading=9,
            alignment=TA_CENTER,
        ),
    }


def _render_alpha_contract(path: Path, fonts: FontFamily) -> None:
    styles = _styles(fonts)
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=15 * mm,
        title="Synthetic Alpha Trade Covenant Contract",
        author="Halyk Covenant MVP — synthetic data only",
    )
    story = [
        Paragraph("СИНТЕТИЧЕСКИЙ КРЕДИТНЫЙ ДОГОВОР", styles["title"]),
        Paragraph("DOCUMENT_ID: alpha_trade_contract.pdf", styles["small"]),
        Paragraph(
            "Заёмщик: <b>ТОО «Альфа Трейд»</b> (в отдельных реестрах — "
            "<b>ALFA TRADE LLP</b>), внутренний идентификатор B001.",
            styles["body"],
        ),
        Paragraph("Раздел 7. Финансовые ковенанты", styles["heading"]),
        Paragraph(
            "7.1. Совокупный месячный <b>обьем</b> исходящих платежей в KZT не должен "
            "превышать <b>15 000,<br/>000 KZT</b>. Валютные операции не суммируются без "
            "утверждённого FX-правила. [COV-ALPHA-SUM]",
            styles["body"],
        ),
        Paragraph(
            "7.2. Один исходящий перевод в KZT не может превышать <b>5.000.000 KZT</b>. "
            "При нарушении указывается конкретная операция. [COV-ALPHA-MAX]",
            styles["body"],
        ),
        Paragraph(
            "7.3. В календарном месяце допускается не более двух исходящих операций, "
            "каждая из которых превышает 4 000 000 KZT. Третья операция считается "
            "триггерной. [COV-ALPHA-COUNT]",
            styles["body"],
        ),
        Paragraph(
            "7.4. Каждое входящее пополнение в KZT должно быть не менее 2 000 000 KZT. "
            "[COV-ALPHA-MIN]",
            styles["body"],
        ),
        Spacer(1, 3 * mm),
        _alpha_summary_table(styles),
        Spacer(1, 11 * mm),
        Paragraph(
            "Дата вступления перечисленных условий в силу указана отдельно: "
            "<b>15 марта 2026 года</b>. Настоящий файл создан только для синтетического "
            "тестирования и не является банковским документом.",
            styles["body"],
        ),
    ]
    document.build(
        story,
        onFirstPage=lambda page, doc: _footer(page, doc, fonts, "SYNTHETIC / page 1"),
        onLaterPages=lambda page, doc: _footer(page, doc, fonts, "SYNTHETIC"),
        canvasmaker=DeterministicCanvas,
    )


def _alpha_summary_table(styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        ["Код", "Метрика", "Условие", "Период"],
        ["COV-ALPHA-SUM", "SUM исходящих KZT", "≤ 15 000 000", "месяц"],
        ["COV-ALPHA-MAX", "MAX одной операции", "≤ 5 000 000", "месяц"],
        ["COV-ALPHA-COUNT", "COUNT операций > 4M", "≤ 2", "месяц"],
        ["COV-ALPHA-MIN", "MIN входящего KZT", "≥ 2 000 000", "месяц"],
    ]
    table = Table(
        [
            [
                Paragraph(str(value), styles["table_header"] if row_index == 0 else styles["table"])
                for value in row
            ]
            for row_index, row in enumerate(rows)
        ],
        colWidths=[38 * mm, 61 * mm, 38 * mm, 26 * mm],
        repeatRows=1,
    )
    table.setStyle(_table_style())
    return table


def _render_limits_appendix(path: Path, fonts: FontFamily) -> None:
    styles = _styles(fonts)
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=15 * mm,
        title="Synthetic Multi-Borrower Limits Appendix",
        author="Halyk Covenant MVP — synthetic data only",
    )
    story = [
        Paragraph("ПРИЛОЖЕНИЕ: ЛИМИТЫ ЗАЁМЩИКОВ", styles["title"]),
        Paragraph("DOCUMENT_ID: borrower_limits_appendix.pdf", styles["small"]),
        Paragraph(
            "Таблица ниже содержит независимо оцениваемые условия. Сокращения и пустые "
            "ячейки воспроизводят вероятные дефекты исходных приложений.",
            styles["body"],
        ),
        _limits_table(
            styles,
            [
                ["B002", "Beta Logistics", "AVG исходящих", "≤ 4 000 000", "KZT", "COV-BETA-AVG"],
                ["B002", "Бета Лог.", "SUM исходящих", "≤ 12 000 000", "KZT", "COV-BETA-SUM"],
                ["B002", "Beta Logistics", "MAX одной операции", "≤ 7 000 000", "", "COV-BETA-MAX"],
            ],
        ),
        Spacer(1, 5 * mm),
        Paragraph(
            "* Пустая валюта в строке MAX означает KZT согласно вводному определению на "
            "этой странице.",
            styles["small"],
        ),
        PageBreak(),
        Paragraph("ЛИМИТЫ ЗАЁМЩИКОВ — ПРОДОЛЖЕНИЕ", styles["heading"]),
        Paragraph(
            "Повтор заголовка таблицы является частью синтетического макета страницы 2.",
            styles["small"],
        ),
        _limits_table(
            styles,
            [
                ["000777", "GAMMA RET.", "SUM исходящих", "≤ 6 млн KZT", "KZT", "COV-GAMMA-SUM"],
            ],
        ),
        Spacer(1, 7 * mm),
        Paragraph(
            "<b>Исключение:</b> налоговые платежи исключаются только при наличии категории "
            "tax в структурированном реестре. В текущем golden CovenantSpec это исключение "
            "не применяется ни к одной строке.",
            styles["body"],
        ),
        Paragraph(
            "Дата действия: 15.03.2026. Документ полностью синтетический; совпадения с "
            "реальными лицами или договорами случайны.",
            styles["body"],
        ),
    ]
    document.build(
        story,
        onFirstPage=lambda page, doc: _footer(page, doc, fonts, "APPENDIX / 1"),
        onLaterPages=lambda page, doc: _footer(page, doc, fonts, "APPENDIX / 2"),
        canvasmaker=DeterministicCanvas,
    )


def _limits_table(styles: dict[str, ParagraphStyle], body_rows: list[list[str]]) -> Table:
    headers = ["ID", "Заёмщик", "Метрика", "Порог", "Валюта", "Код"]
    rows = [headers, *body_rows]
    table = Table(
        [
            [
                Paragraph(value, styles["table_header"] if row_index == 0 else styles["table"])
                for value in row
            ]
            for row_index, row in enumerate(rows)
        ],
        colWidths=[20 * mm, 35 * mm, 42 * mm, 31 * mm, 19 * mm, 37 * mm],
        repeatRows=1,
    )
    table.setStyle(_table_style())
    return table


def _table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006B57")),
            ("GRID", (0, 0), (-1, -1), 0.65, colors.HexColor("#4A4A4A")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EAF4F1")]),
        ]
    )


def _footer(page: canvas.Canvas, document: object, fonts: FontFamily, label: str) -> None:
    del document
    page.saveState()
    page.setFont(fonts.regular, 6.8)
    page.setFillColor(colors.HexColor("#555555"))
    page.drawString(18 * mm, 9 * mm, f"{label} — synthetic benchmark artifact")
    page.restoreState()
