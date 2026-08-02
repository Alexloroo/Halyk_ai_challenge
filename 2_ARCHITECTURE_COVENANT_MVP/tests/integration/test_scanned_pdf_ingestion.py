from pathlib import Path

import fitz

from halyk_covenants.domain import DocumentBlock, SourceRef
from halyk_covenants.ingestion.pdf import PDFIngestor

PROJECT_ROOT = Path(__file__).parents[2]
ALPHA_PDF = PROJECT_ROOT / "data/synthetic/documents/alpha_trade_contract.pdf"


class FixtureOCR:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, image: bytes, *, document_id: str, page: int) -> list[DocumentBlock]:
        self.calls += 1
        bbox = (10.0, 10.0, 400.0, 50.0)
        return [
            DocumentBlock(
                block_id=f"OCR-{page}",
                document_id=document_id,
                page=page,
                block_type="text",
                text="Месячный объём исходящих платежей не более 15 000 000 KZT",
                bbox=bbox,
                extraction_method="ocr",
                confidence="0.99",
                source=SourceRef(document_id=document_id, page=page, bbox=bbox),
            )
        ]


def make_scanned_pdf(target: Path) -> None:
    with fitz.open(ALPHA_PDF) as source:
        png = source[0].get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False).tobytes("png")
    scanned = fitz.open()
    page = scanned.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=png)
    scanned.save(target)
    scanned.close()


def test_scanned_pdf_routes_through_ocr(tmp_path: Path) -> None:
    scanned = tmp_path / "scanned.pdf"
    make_scanned_pdf(scanned)
    ocr = FixtureOCR()

    blocks = PDFIngestor(ocr=ocr).ingest(scanned)

    assert ocr.calls == 1
    assert blocks[0].extraction_method == "ocr"
    assert "15 000 000" in blocks[0].text


def test_pdf_without_layout_provider_does_not_probe_tables(monkeypatch) -> None:
    def fail_if_called(page):
        raise AssertionError("table detection is unnecessary without a layout provider")

    monkeypatch.setattr(PDFIngestor, "_table_count", staticmethod(fail_if_called))

    blocks = PDFIngestor().ingest(ALPHA_PDF)

    assert blocks
