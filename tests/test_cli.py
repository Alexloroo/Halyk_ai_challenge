from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pymupdf

from halyk.cli import main
from halyk.tracing import TraceWriter


def _dataset(root: Path) -> None:
    (root / "documents").mkdir(parents=True)
    (root / "submission_template.json").write_text(
        json.dumps({"team": "", "contact_email": "", "model": "", "answers": {"P1": {"6.1": {}}}}),
        encoding="utf-8",
    )
    (root / "master_ledger_2025.csv").write_text(
        "txn_id,date,account_id,counterparty,description,amount,currency\n"
        "TXN-P1-0001,2025-02-01,ACC-0001,Customer,service revenue,100.00,USD\n",
        encoding="utf-8",
    )
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "ACC-0001 operations note")
        pdf.save(root / "documents" / "one.pdf")


def test_cli_only_recreates_trace_when_fulltrace_is_enabled(tmp_path: Path) -> None:
    data_dir = tmp_path / "raw"
    _dataset(data_dir)
    trace_dir = tmp_path / "trace"
    TraceWriter.create(trace_dir)
    sentinel = trace_dir / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    normal_output = tmp_path / "normal.json"
    assert main(["--data-dir", str(data_dir), "--output", str(normal_output), "--no-llm"]) == 0
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert normal_output.exists()

    traced_output = tmp_path / "traced.json"
    assert main(
        [
            "--data-dir",
            str(data_dir),
            "--output",
            str(traced_output),
            "--trace-dir",
            str(trace_dir),
            "--no-llm",
            "--fulltrace",
        ]
    ) == 0
    assert not sentinel.exists()
    traced_submission = json.loads(
        (trace_dir / "13_submission" / "submission.json").read_text(encoding="utf-8")
    )
    assert traced_submission == json.loads(traced_output.read_text(encoding="utf-8"))
    manifest = json.loads((trace_dir / "manifest.json").read_text(encoding="utf-8"))
    submission_stage = manifest["stages"][-1]
    assert submission_stage["name"] == "13_submission"
    assert submission_stage["scenarios"] == 1
    assert submission_stage["cells"] == 1


def test_makefile_run_and_fulltrace_targets_execute_the_pipeline(tmp_path: Path) -> None:
    data_dir = tmp_path / "raw"
    _dataset(data_dir)
    project = Path(__file__).resolve().parents[1]
    normal_output = tmp_path / "make-normal.json"

    subprocess.run(
        [
            "make",
            "run",
            f"DATA_DIR={data_dir}",
            f"OUTPUT={normal_output}",
            "ARGS=--no-llm",
        ],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    assert normal_output.exists()

    trace_dir = tmp_path / "make-trace"
    traced_output = tmp_path / "make-traced.json"
    subprocess.run(
        [
            "make",
            "fulltrace",
            f"DATA_DIR={data_dir}",
            f"OUTPUT={traced_output}",
            f"TRACE_DIR={trace_dir}",
            "ARGS=--no-llm",
        ],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    assert traced_output.exists()
    assert (trace_dir / "13_submission/submission.json").exists()
