from __future__ import annotations

from collections.abc import Callable
from typing import Any

from halyk_covenants.domain import DocumentBlock
from halyk_covenants.observability import trace_stage
from halyk_covenants.ocr import PaddleOCRProvider


class PaddleLayoutProvider:
    """Local layout/VLM adapter with OCR normalization as a conservative fallback."""

    def __init__(
        self,
        *,
        pipeline_factory: Callable[[str], Any] | None = None,
        preferred_device: str = "gpu:0",
    ) -> None:
        self.pipeline_factory = pipeline_factory or self._default_pipeline_factory
        self.preferred_device = preferred_device
        self._normalizer = PaddleOCRProvider(preferred_device=preferred_device)

    @trace_stage("vlm.paddle_layout", run_type="tool", redact_inputs={"image"})
    def extract(self, image: bytes, *, document_id: str, page: int) -> list[DocumentBlock]:
        pipeline = self.pipeline_factory(self.preferred_device)
        raw = pipeline.predict(image)
        lines = self._normalizer._normalize_lines(raw)
        blocks = [
            self._normalizer._to_block(line, document_id, page, index)
            for index, line in enumerate(lines)
        ]
        for block in blocks:
            block.block_type = "table_cell"
            block.extraction_method = "layout"
        return blocks

    @staticmethod
    def _default_pipeline_factory(device: str) -> Any:
        try:
            from paddleocr import PPStructureV3
        except ImportError as exc:
            raise RuntimeError("PaddleOCR layout pipeline is not installed") from exc
        return PPStructureV3(device=device)
