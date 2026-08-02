import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is not installed")
def test_gpu_profile_has_device_access_memory_and_model_cache() -> None:
    completed = subprocess.run(
        ["docker", "compose", "--profile", "gpu", "config", "--format", "json"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    service = json.loads(completed.stdout)["services"]["ocr-gpu"]
    assert service["profiles"] == ["gpu"]
    assert service["shm_size"] == "8589934592"
    assert service["healthcheck"]["test"] == ["CMD", "/app/scripts/ocr-healthcheck.sh"]
    assert any(volume["target"] == "/home/appuser/.paddlex" for volume in service["volumes"])
    reservations = service["deploy"]["resources"]["reservations"]["devices"]
    assert reservations[0]["capabilities"] == ["gpu"]


def test_cuda_image_reuses_preexisting_uid_and_gid_1000() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile.ocr").read_text(encoding="utf-8")

    assert "groupmod --new-name appuser ubuntu" in dockerfile
    assert "usermod --login appuser --home /home/appuser --move-home ubuntu" in dockerfile
    assert "groupadd --gid 1000 appuser" not in dockerfile


def test_paddlex_yaml_constraint_and_gpu_wheel_have_separate_layers() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile.ocr").read_text(encoding="utf-8")
    project = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"pyyaml==6.0.2"' in project
    assert '"paddleocr==3.4.1"' in project
    assert '"paddleocr==3.4.1"' in dockerfile
    assert "FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04" in dockerfile
    assert "--no-deps paddlepaddle-gpu==3.3.0" in dockerfile
    assert "packages/stable/cu129/" in dockerfile
    assert "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True" in dockerfile
    assert dockerfile.count("RUN python -m pip install") >= 2
