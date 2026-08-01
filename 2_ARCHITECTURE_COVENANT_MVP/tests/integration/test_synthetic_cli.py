import json
from pathlib import Path

from typer.testing import CliRunner

from halyk_covenants.cli import app


runner = CliRunner()


def test_generate_synthetic_command_creates_valid_dataset(tmp_path: Path) -> None:
    output = tmp_path / "synthetic"

    invocation = runner.invoke(app, ["generate-synthetic", "--output", str(output)])

    assert invocation.exit_code == 0, invocation.output
    payload = json.loads(invocation.stdout)
    assert payload["dataset_version"] == "2026.08.02-v1"
    assert len(payload["artifacts"]) == 14
    assert (output / "documents" / "alpha_trade_contract.pdf").is_file()
    assert (output / "transactions" / "synthetic_transactions.xlsx").is_file()


def test_benchmark_command_writes_reports_and_prints_summary(tmp_path: Path) -> None:
    output = tmp_path / "synthetic"
    assert runner.invoke(app, ["generate-synthetic", "--output", str(output)]).exit_code == 0

    invocation = runner.invoke(app, ["benchmark", "--dataset", str(output)])

    assert invocation.exit_code == 0, invocation.output
    payload = json.loads(invocation.stdout)
    assert payload["summary"]["earned_components"] == 29
    assert payload["summary"]["maximum_components"] == 30
    assert payload["summary"]["component_accuracy"] == "0.9666666666666666666666666667"
    assert Path(payload["report_json"]).is_file()
    assert Path(payload["report_markdown"]).is_file()


def test_benchmark_command_exits_nonzero_when_minimum_accuracy_is_not_met(
    tmp_path: Path,
) -> None:
    output = tmp_path / "synthetic"
    assert runner.invoke(app, ["generate-synthetic", "--output", str(output)]).exit_code == 0

    invocation = runner.invoke(
        app,
        [
            "benchmark",
            "--dataset",
            str(output),
            "--min-component-accuracy",
            "1.0",
        ],
    )

    assert invocation.exit_code == 3
    assert "below required minimum" in invocation.stderr
    assert (output / "benchmark" / "report.json").is_file()
