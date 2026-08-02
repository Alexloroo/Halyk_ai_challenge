import json
from pathlib import Path

from typer.testing import CliRunner

from halyk_covenants.cli import app

runner = CliRunner()


def test_serialize_and_validate_submission_cli(tmp_path: Path) -> None:
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            [
                {
                    "borrower_id": "B1",
                    "covenant_id": "C1",
                    "verdict": "violated",
                    "number": "0.34",
                    "number_unit": "ratio",
                    "status": "success",
                }
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "submission.json"
    profile = Path("configs/submission/synthetic.yaml").resolve()

    serialized = runner.invoke(
        app,
        [
            "serialize-submission",
            "--results",
            str(results),
            "--profile",
            str(profile),
            "--output",
            str(output),
        ],
    )
    validated = runner.invoke(
        app,
        ["validate-submission", "--submission", str(output), "--profile", str(profile)],
    )

    assert serialized.exit_code == 0, serialized.output
    assert json.loads(output.read_text(encoding="utf-8"))["answers"][0]["number"] == "34"
    assert validated.exit_code == 0, validated.output
    assert json.loads(validated.stdout)["valid"] is True
