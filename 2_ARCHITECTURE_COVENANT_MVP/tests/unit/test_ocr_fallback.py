from io import BytesIO

import numpy as np
from PIL import Image

from halyk_covenants.ocr import OCRLine, PaddleOCRProvider
from halyk_covenants.ocr import paddle as paddle_module


class RecordingEngine:
    def __init__(self, device: str, devices: list[str]) -> None:
        self.device = device
        self.devices = devices
        self.devices.append(device)

    def predict(self, image: bytes):
        if self.device == "gpu:0":
            raise MemoryError("CUDA out of memory")
        return [OCRLine(text="Лимит 5 000 000 KZT", bbox=(1, 2, 100, 20), confidence="0.98")]


def test_cuda_oom_retries_same_page_on_cpu() -> None:
    devices: list[str] = []
    provider = PaddleOCRProvider(
        engine_factory=lambda device: RecordingEngine(device, devices),
        preferred_device="gpu:0",
    )

    blocks = provider.extract(b"png", document_id="DOC1", page=1)

    assert blocks[0].text == "Лимит 5 000 000 KZT"
    assert blocks[0].extraction_method == "ocr"
    assert devices == ["gpu:0", "cpu"]


def test_non_cuda_error_is_not_retried_on_cpu() -> None:
    devices: list[str] = []

    class BrokenEngine(RecordingEngine):
        def predict(self, image: bytes):
            raise ValueError("invalid image format")

    provider = PaddleOCRProvider(
        engine_factory=lambda device: BrokenEngine(device, devices),
        preferred_device="gpu:0",
    )

    try:
        provider.extract(b"bad", document_id="DOC1", page=1)
    except ValueError as exc:
        assert str(exc) == "invalid image format"
    else:
        raise AssertionError("invalid input must fail")

    assert devices == ["gpu:0"]


def test_engine_is_initialized_once_per_device() -> None:
    devices: list[str] = []

    class SuccessfulEngine(RecordingEngine):
        def predict(self, image: bytes):
            return [OCRLine(text=image.decode(), bbox=(1, 2, 100, 20), confidence="0.98")]

    provider = PaddleOCRProvider(
        engine_factory=lambda device: SuccessfulEngine(device, devices),
        preferred_device="gpu:0",
    )

    first = provider.extract(b"first", document_id="DOC1", page=1)
    second = provider.extract(b"second", document_id="DOC1", page=2)

    assert first[0].text == "first"
    assert second[0].text == "second"
    assert devices == ["gpu:0"]


def test_paddle_adapter_decodes_png_bytes_to_numpy_array() -> None:
    received: list[np.ndarray] = []

    class ArrayEngine:
        def predict(self, image):
            received.append(image)
            return []

    stream = BytesIO()
    Image.new("RGB", (3, 2), color=(10, 20, 30)).save(stream, format="PNG")

    assert hasattr(paddle_module, "PaddlePredictAdapter")
    adapter_class = paddle_module.PaddlePredictAdapter
    adapter_class(ArrayEngine()).predict(stream.getvalue())

    assert len(received) == 1
    assert isinstance(received[0], np.ndarray)
    assert received[0].shape == (2, 3, 3)
    assert received[0][0, 0].tolist() == [30, 20, 10]
