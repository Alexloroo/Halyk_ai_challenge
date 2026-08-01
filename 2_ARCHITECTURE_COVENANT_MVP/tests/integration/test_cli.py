import json
from pathlib import Path

from typer.testing import CliRunner

from halyk_covenants.cli import app

runner = CliRunner()


def write_covenant(path: Path, metric_type: str, threshold: str, evidence_mode: str) -> None:
    path.write_text(
        json.dumps(
            {
                "covenant_id": f"COV-{metric_type.upper()}",
                "raw_text": "Synthetic covenant",
                "borrower_ids": ["B001"],
                "metric": {"metric_type": metric_type, "field": "amount", "unit": "KZT"},
                "condition": {"comparator": "<=", "threshold": threshold, "currency": "KZT"},
                "transaction_filters": [
                    {"field": "direction", "operator": "eq", "value": "outgoing"}
                ],
                "time_window": {"type": "calendar_month"},
                "evidence_mode": evidence_mode,
                "source": {"document_id": "fixture", "page": 1},
                "confidence": 1,
            }
        ),
        encoding="utf-8",
    )


def test_cli_evaluates_aggregate_16m_scenario(tmp_path: Path) -> None:
    transactions = tmp_path / "transactions.csv"
    transactions.write_text(
        "transaction_id,borrower_id,date,amount,currency,direction\n"
        "TX1,B001,2026-04-01,5000000,KZT,outgoing\n"
        "TX2,B001,2026-04-10,6000000,KZT,outgoing\n"
        "TX3,B001,2026-04-20,5000000,KZT,outgoing\n",
        encoding="utf-8",
    )
    covenant = tmp_path / "sum.json"
    write_covenant(covenant, "sum", "15000000", "none")

    invocation = runner.invoke(
        app,
        [
            "evaluate",
            "--transactions",
            str(transactions),
            "--covenant",
            str(covenant),
            "--borrower-id",
            "B001",
            "--at-date",
            "2026-04-30",
        ],
    )

    assert invocation.exit_code == 0, invocation.output
    payload = json.loads(invocation.stdout)
    assert payload["number"] == "16000000.000000"
    assert payload["verdict"] == "violated"
    assert payload["evidence_transaction_id"] is None
    assert payload["status"] == "success"


def test_cli_evaluates_max_scenario_with_tx2_evidence(tmp_path: Path) -> None:
    transactions = tmp_path / "transactions.csv"
    transactions.write_text(
        "transaction_id,borrower_id,date,amount,currency,direction\n"
        "TX1,B001,2026-04-01,4000000,KZT,outgoing\n"
        "TX2,B001,2026-04-10,6000000,KZT,outgoing\n"
        "TX3,B001,2026-04-20,3000000,KZT,outgoing\n",
        encoding="utf-8",
    )
    covenant = tmp_path / "max.json"
    write_covenant(covenant, "max", "5000000", "violating_transaction")

    invocation = runner.invoke(
        app,
        [
            "evaluate",
            "--transactions",
            str(transactions),
            "--covenant",
            str(covenant),
            "--borrower-id",
            "B001",
            "--at-date",
            "2026-04-30",
        ],
    )

    assert invocation.exit_code == 0, invocation.output
    payload = json.loads(invocation.stdout)
    assert payload["number"] == "6000000.000000"
    assert payload["verdict"] == "violated"
    assert payload["evidence_transaction_id"] == "TX2"


def test_cli_returns_nonzero_exit_for_invalid_covenant(tmp_path: Path) -> None:
    transactions = tmp_path / "transactions.csv"
    transactions.write_text(
        "transaction_id,borrower_id,date,amount\nTX1,B001,2026-04-01,1\n",
        encoding="utf-8",
    )
    covenant = tmp_path / "invalid.json"
    covenant.write_text("{}", encoding="utf-8")

    invocation = runner.invoke(
        app,
        [
            "evaluate",
            "--transactions",
            str(transactions),
            "--covenant",
            str(covenant),
            "--borrower-id",
            "B001",
        ],
    )

    assert invocation.exit_code == 2
    assert "Invalid covenant specification" in invocation.stderr
