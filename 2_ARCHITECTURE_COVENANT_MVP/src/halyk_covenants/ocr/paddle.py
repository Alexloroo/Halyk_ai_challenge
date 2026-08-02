from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Callable, Iterable
from io import BytesIO
from typing import Any, Protocol

from halyk_covenants.domain import DocumentBlock, SourceRef
from halyk_covenants.observability import trace_stage
from halyk_covenants.ocr.base import OCRLine


class PaddleEngine(Protocol):
    def predict(self, image: bytes) -> Any: ...


EngineFactory = Callable[[str], PaddleEngine]


class PaddlePredictAdapter:
    """Translate the pipeline's encoded PNG bytes to PaddleOCR's ndarray input."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def predict(self, image: bytes) -> Any:
        try:
            import numpy as np
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("PaddleOCR image decoding requires numpy and Pillow") from exc

        with Image.open(BytesIO(image)) as source:
            rgb = np.asarray(source.convert("RGB"))
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        return self.engine.predict(bgr)


class PaddleOCRProvider:
    def __init__(
        self,
        *,
        engine_factory: EngineFactory | None = None,
        preferred_device: str = "gpu:0",
        cpu_fallback: bool = True,
    ) -> None:
        self.engine_factory = engine_factory or self._default_engine_factory
        self.preferred_device = preferred_device
        self.cpu_fallback = cpu_fallback
        self._engines: dict[str, PaddleEngine] = {}

    def validate_runtime(self) -> None:
        missing = [
            package
            for package in ("paddle", "paddleocr")
            if importlib.util.find_spec(package) is None
        ]
        if missing:
            packages = ", ".join(missing)
            raise RuntimeError(
                f"OCR was requested, but the local runtime is missing: {packages}. "
                "Run the GPU OCR Docker profile or install PaddlePaddle and paddleocr."
            )

    def _engine(self, device: str) -> PaddleEngine:
        engine = self._engines.get(device)
        if engine is None:
            engine = self.engine_factory(device)
            self._engines[device] = engine
        return engine

    @trace_stage("ocr.paddle_gpu", run_type="tool", redact_inputs={"image"})
    def extract(self, image: bytes, *, document_id: str, page: int) -> list[DocumentBlock]:
        try:
            raw = self._engine(self.preferred_device).predict(image)
        except Exception as exc:
            if not self.cpu_fallback or not self._is_gpu_runtime_error(exc):
                raise
            raw = self._extract_cpu(image)
        lines = self._normalize_lines(raw)
        return [self._to_block(line, document_id, page, index) for index, line in enumerate(lines)]

    @trace_stage("ocr.paddle_cpu_fallback", run_type="tool", redact_inputs={"image"})
    def _extract_cpu(self, image: bytes) -> Any:
        return self._engine("cpu").predict(image)

    @staticmethod
    def _default_engine_factory(device: str) -> PaddleEngine:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR is not installed; use the OCR Docker profile or install the ocr extra"
            ) from exc
        return PaddlePredictAdapter(
            PaddleOCR(
                device=device,
                text_recognition_model_name="cyrillic_PP-OCRv5_mobile_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        )

    @staticmethod
    def _is_gpu_runtime_error(exc: Exception) -> bool:
        if isinstance(exc, MemoryError):
            return True
        message = str(exc).casefold()
        return any(token in message for token in ("cuda", "cudnn", "out of memory", "gpu"))

    @classmethod
    def _normalize_lines(cls, raw: Any) -> list[OCRLine]:
        if raw is None:
            return []
        if isinstance(raw, OCRLine):
            return [raw]
        if isinstance(raw, dict):
            direct = cls._lines_from_mapping(raw)
            if direct:
                return direct
            lines: list[OCRLine] = []
            for value in raw.values():
                lines.extend(cls._normalize_lines(value))
            return lines
        if hasattr(raw, "json"):
            payload = raw.json() if callable(raw.json) else raw.json
            return cls._normalize_lines(payload)
        if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
            items = list(raw)
            if items and all(isinstance(item, OCRLine) for item in items):
                return items
            lines: list[OCRLine] = []
            for item in items:
                lines.extend(cls._normalize_lines(item))
            return lines
        return []

    @staticmethod
    def _lines_from_mapping(payload: dict[str, Any]) -> list[OCRLine]:
        data = payload.get("res", payload)
        if not isinstance(data, dict):
            return []
        texts = data.get("rec_texts", [])
        scores = data.get("rec_scores", [])
        polygons = data.get("rec_polys", data.get("dt_polys", []))
        lines: list[OCRLine] = []
        for index, text in enumerate(texts):
            polygon = polygons[index] if index < len(polygons) else [(0, 0), (0, 0)]
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
            bbox = (min(xs), min(ys), max(xs), max(ys)) if xs and ys else (0, 0, 0, 0)
            score = scores[index] if index < len(scores) else 0
            lines.append(OCRLine(text=str(text), bbox=bbox, confidence=score))
        return lines

    @staticmethod
    def _to_block(line: OCRLine, document_id: str, page: int, index: int) -> DocumentBlock:
        key = f"{document_id}:{page}:ocr:{index}:{line.text}:{line.bbox}"
        block_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        source = SourceRef(document_id=document_id, page=page, bbox=line.bbox)
        return DocumentBlock(
            block_id=block_id,
            document_id=document_id,
            page=page,
            block_type="text",
            text=line.text,
            bbox=line.bbox,
            extraction_method="ocr",
            confidence=line.confidence,
            source=source,
        )
