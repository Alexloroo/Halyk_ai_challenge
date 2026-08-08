"""Create X23/X24 DeepSeek formula fixtures and non-binding PDF distractors.

Run manually when rebuilding the synthetic raw dataset.  The generated PDFs
contain native text deliberately: this fixture tests document selection and
formula interpretation, not OCR.
"""

from pathlib import Path

import pymupdf

DOCUMENTS = Path(__file__).resolve().parents[1] / "data" / "raw" / "documents"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


PDFS = {
    "a3f07c91b4d2.pdf": """\
CREDIT AGREEMENT
EXECUTION COPY
Borrower: Altai Renewable Systems JSC
Account: ACC-8823

Article 6. Financial Covenants

6.1 Operating Cost Intensity Ratio.
For the period from 2025-01-01 to 2025-12-31, the aggregate of marketing and
professional-service payments must not exceed 0.20x of Revenue.

6.2 Revenue Cushion.
For the same period, Revenue less the larger of personnel expenses and utility
expenses must be at least USD 800,000.

6.3 Minimum Revenue.
For the period from 2025-01-01 to 2025-12-31, Revenue must not fall below
USD 1,000,000.

Article 7. Miscellaneous
This executed agreement is binding on the Borrower and the Bank.
""",
    "b7e1490c6fa8.pdf": """\
БАНКТІК ҚАРЫЗ ШАРТЫ
ОРЫНДАУ ДАНАСЫ
Қарыз алушы: Zhetysu Solar Components JSC
Шот: ACC-8824

6-бап. Қаржылық ковенанттар

6.1 Шығындардың өтелу коэффициенті.
2025-01-01 бастап 2025-12-31 дейін маркетинг және кәсіби қызметтер бойынша
шығыстардың түсімге арақатынасы 0.25x-тен аспауға тиіс.

6.2 Түсімнің ең төменгі деңгейі.
2025-01-01 бастап 2025-12-31 дейін түсім USD 1,000,000-нан кем болмауға тиіс.

6.3 Маркетинг шығындарының лимиті.
2025-01-01 бастап 2025-12-31 дейін маркетинг шығындары USD 250,000-нан аспауға
тиіс.

7-бап. Қорытынды ережелер
Осы орындау данасы Банк пен Қарыз алушы үшін міндетті болып табылады.
""",
    "c9d2e7a184f0.pdf": """\
INTERNAL TRAINING MEMO — NOT A CREDIT AGREEMENT
Illustrative training example only. It creates no obligations and must not be
used for covenant monitoring.
Account example: ACC-8823

Article 6. Sample Covenants
6.1 Marketing expenditure must not exceed 0.01x of Revenue.
6.2 Revenue must be at least USD 99,000,000.
6.3 Personnel expenses must not exceed USD 1.
""",
    "d4a8f1e903bc.pdf": """\
PROJECT SUNFLOWER — WEEKLY OFFICE UPDATE
Working notes for the facilities team. This document is not a loan agreement,
does not create obligations, and is not an approved financial record.
Account reference: ACC-8824

The team discussed an illustrative 6.1 ratio and a potential advertising
budget. No covenant, threshold, audit adjustment, or legal term is approved.
""",
}


def _write_pdf(path: Path, text: str) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    rectangle = pymupdf.Rect(48, 48, 547, 794)
    remaining = page.insert_textbox(
        rectangle,
        text,
        fontname="DejaVuSans",
        fontfile=FONT,
        fontsize=10,
        lineheight=1.35,
    )
    if remaining < 0:
        raise RuntimeError(f"Fixture text does not fit on page: {path.name}")
    pdf.save(path)
    pdf.close()


def main() -> None:
    DOCUMENTS.mkdir(parents=True, exist_ok=True)
    for filename, text in PDFS.items():
        _write_pdf(DOCUMENTS / filename, text)


if __name__ == "__main__":
    main()
