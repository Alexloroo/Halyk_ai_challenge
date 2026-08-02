from __future__ import annotations

import hashlib
from collections.abc import Callable
from statistics import median
from typing import Any

from halyk_covenants.domain import DocumentBlock
from halyk_covenants.observability import trace_stage
from halyk_covenants.ocr import PaddleOCRProvider
from halyk_covenants.ocr.paddle import PaddlePredictAdapter


class PaddleLayoutProvider:
    """Local PPStructure adapter that preserves a deterministic table-cell representation."""

    def __init__(
        self,
        *,
        pipeline_factory: Callable[[str], Any] | None = None,
        preferred_device: str = "gpu:0",
    ) -> None:
        self.pipeline_factory = pipeline_factory or self._default_pipeline_factory
        self.preferred_device = preferred_device
        self._normalizer = PaddleOCRProvider(preferred_device=preferred_device)
        self._pipeline: Any | None = None

    def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            self._pipeline = self.pipeline_factory(self.preferred_device)
        return self._pipeline

    @trace_stage("vlm.paddle_layout", run_type="tool", redact_inputs={"image"})
    def extract(self, image: bytes, *, document_id: str, page: int) -> list[DocumentBlock]:
        raw = self._get_pipeline().predict(image)
        lines = self._normalizer._normalize_lines(raw)
        blocks = [
            self._normalizer._to_block(line, document_id, page, index)
            for index, line in enumerate(lines)
        ]
        return self._assign_table_coordinates(blocks, document_id=document_id, page=page)

    @staticmethod
    def _assign_table_coordinates(
        blocks: list[DocumentBlock], *, document_id: str, page: int
    ) -> list[DocumentBlock]:
        if not blocks:
            return []
        heights = [
            max((block.bbox[3] - block.bbox[1]) if block.bbox else 0.0, 1.0)
            for block in blocks
        ]
        row_tolerance = max(4.0, median(heights) * 0.65)
        ordered = sorted(
            blocks,
            key=lambda item: (
                ((item.bbox[1] + item.bbox[3]) / 2) if item.bbox else 0.0,
                item.bbox[0] if item.bbox else 0.0,
                item.block_id,
            ),
        )
        rows: list[list[DocumentBlock]] = []
        row_centers: list[float] = []
        for block in ordered:
            center = ((block.bbox[1] + block.bbox[3]) / 2) if block.bbox else 0.0
            if not rows or abs(center - row_centers[-1]) > row_tolerance:
                rows.append([block])
                row_centers.append(center)
            else:
                rows[-1].append(block)
                row_centers[-1] = sum(
                    ((item.bbox[1] + item.bbox[3]) / 2) if item.bbox else 0.0
                    for item in rows[-1]
                ) / len(rows[-1])

        table_digest = hashlib.sha256(f"{document_id}:{page}:layout-table".encode()).hexdigest()[:16]
        table_id = f"table-{table_digest}"
        output: list[DocumentBlock] = []
        for row_index, row in enumerate(rows):
            for column_index, block in enumerate(
                sorted(row, key=lambda item: (item.bbox[0] if item.bbox else 0.0, item.block_id))
            ):
                output.append(
                    block.model_copy(
                        update={
                            "block_type": "table_cell",
                            "extraction_method": "layout",
                            "table_id": table_id,
                            "row_index": row_index,
                            "column_index": column_index,
                            "source": block.source.model_copy(update={"table_id": table_id}),
                        }
                    )
                )
        return output

    @staticmethod
    def _default_pipeline_factory(device: str) -> Any:
        try:
            from paddleocr import PPStructureV3
        except ImportError as exc:
            raise RuntimeError("PaddleOCR layout pipeline is not installed") from exc
        return PaddlePredictAdapter(PPStructureV3(device=device))
