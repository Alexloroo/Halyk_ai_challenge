import json
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parents[2]


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is not installed")
def test_compose_config_sequences_two_services_on_one_image_and_shared_dataset() -> None:
    completed = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    config = json.loads(completed.stdout)
    services = config["services"]
    assert set(services) == {"generate-synthetic", "benchmark"}
    assert {service["image"] for service in services.values()} == {
        "halyk-covenants:synthetic"
    }
    assert services["benchmark"]["depends_on"]["generate-synthetic"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["generate-synthetic"]["command"] == [
        "generate-synthetic",
        "--output",
        "/app/data/synthetic",
    ]
    assert services["benchmark"]["command"] == [
        "benchmark",
        "--dataset",
        "/app/data/synthetic",
    ]
    assert services["benchmark"]["user"] == "appuser"
    assert services["benchmark"]["healthcheck"]["test"] == [
        "CMD",
        "/app/scripts/docker-healthcheck.sh",
    ]
    volume_targets = {
        volume["target"] for volume in services["benchmark"]["volumes"]
    }
    # Mount the parent: the generator atomically swaps the synthetic directory,
    # which is impossible when that directory is itself a mount point.
    assert volume_targets == {"/app/data"}
