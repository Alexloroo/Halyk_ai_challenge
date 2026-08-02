from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Protocol

import fitz

from halyk_covenants.domain import DocumentBlock, SourceRef
from halyk_covenants.ingestion.quality import NativePage, PageQualityRouter
from halyk_covenants.observability import trace_stage


class PageExtractor(Protocol):
    def extract(self, image: bytes, *, document_id: str, page: int) -> list[DocumentBlock]: ...


class PDFIngestor:
    def __init__(
        self,
        *,
        router: PageQualityRouter | None = None,
        ocr: PageExtractor | Any | None = None,
        visual: PageExtractor | Any | None = None,
    ) -> None:
        self.router = router or PageQualityRouter()
        self.ocr = ocr
        self.visual = visual

    @trace_stage("pipeline.preprocess.pdf", run_type="chain", redact_inputs={"raw_pdf"})
    def ingest(self, path: Path) -> list[DocumentBlock]:
        document_bytes = path.read_bytes()
        document_id = hashlib.sha256(document_bytes).hexdigest()
        blocks: list[DocumentBlock] = []
        with fitz.open(stream=document_bytes, filetype="pdf") as document:
            for page_index, page in enumerate(document, start=1):
                text = page.get_text("text")
                quality = self.router.classify(
                    NativePage(
                        page=page_index,
                        text=text,
                        image_count=len(page.get_images(full=True)),
                        table_count=self._table_count(page) if self.visual is not None else 0,
                        width=page.rect.width,
                        height=page.rect.height,
                    )
                )
                native_blocks = self._extract_native(page, document_id, page_index)
                if quality.route == "native":
                    blocks.extend(native_blocks)
                    continue
                image = self._render_page(page)
                if quality.route == "layout" and self.visual is not None:
                    blocks.extend(
                        self.visual.extract(image, document_id=document_id, page=page_index)
                    )
                elif self.ocr is not None:
                    blocks.extend(self.ocr.extract(image, document_id=document_id, page=page_index))
                elif native_blocks:
                    blocks.extend(native_blocks)
        return blocks

    @trace_stage("pdf.extract_native", run_type="tool")
    def _extract_native(
        self,
        page: fitz.Page,
        document_id: str,
        page_number: int,
    ) -> list[DocumentBlock]:
        output: list[DocumentBlock] = []
        for item in page.get_text("blocks"):
            x0, y0, x1, y1, text, block_number, block_type = item[:7]
            normalized = str(text).strip()
            if block_type != 0 or not normalized:
                continue
            bbox = (float(x0), float(y0), float(x1), float(y1))
            block_key = f"{document_id}:{page_number}:{block_number}:{normalized}"
            block_id = hashlib.sha256(block_key.encode("utf-8")).hexdigest()[:24]
            source = SourceRef(document_id=document_id, page=page_number, bbox=bbox)
            output.append(
                DocumentBlock(
                    block_id=block_id,
                    document_id=document_id,
                    page=page_number,
                    block_type="text",
                    text=normalized,
                    bbox=bbox,
                    extraction_method="native",
                    confidence="1",
                    source=source,
                )
            )
        return output

    @staticmethod
    def _render_page(page: fitz.Page) -> bytes:
        return page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")

    @staticmethod
    def _table_count(page: fitz.Page) -> int:
        try:
            return len(page.find_tables().tables)
        except Exception:
            return 0
