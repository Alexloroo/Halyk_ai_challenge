import os

import pytest


@pytest.mark.skipif(os.getenv("RUN_GPU_OCR_LIVE") != "1", reason="opt-in GPU OCR live test")
def test_paddle_cuda_is_available() -> None:
    paddle = pytest.importorskip("paddle")
    assert paddle.device.is_compiled_with_cuda()
    assert paddle.device.cuda.device_count() >= 1
