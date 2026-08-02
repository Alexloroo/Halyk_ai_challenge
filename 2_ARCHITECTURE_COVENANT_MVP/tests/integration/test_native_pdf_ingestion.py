from pathlib import Path

from halyk_covenants.ingestion.pdf import PDFIngestor

PROJECT_ROOT = Path(__file__).parents[2]
ALPHA_PDF = PROJECT_ROOT / "data/synthetic/documents/alpha_trade_contract.pdf"


class FailIfCalledOCR:
    def extract(self, *args, **kwargs):
        raise AssertionError("OCR must not be called for a readable native PDF")


def test_native_contract_uses_text_without_ocr() -> None:
    blocks = PDFIngestor(ocr=FailIfCalledOCR()).ingest(ALPHA_PDF)

    assert any("Финансовые ковенанты" in block.text for block in blocks)
    assert {block.extraction_method for block in blocks} == {"native"}


def test_native_blocks_keep_document_page_and_bbox_provenance() -> None:
    blocks = PDFIngestor().ingest(ALPHA_PDF)

    assert blocks
    assert all(block.document_id for block in blocks)
    assert all(block.page == 1 for block in blocks)
    assert all(block.source.page == block.page for block in blocks)
    assert all(block.bbox == block.source.bbox for block in blocks)
    assert len({block.block_id for block in blocks}) == len(blocks)
