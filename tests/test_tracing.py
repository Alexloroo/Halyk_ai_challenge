from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from halyk.categorize import Category
from halyk.tracing import TraceWriter
from halyk.tracing.writer import is_unsafe_trace_root


class SampleModel(BaseModel):
    label: str
    value: Decimal


def test_create_replaces_previous_trace_and_rejects_project_root(tmp_path: Path) -> None:
    trace_root = tmp_path / "trace"
    TraceWriter.create(trace_root)
    (trace_root / "stale.txt").write_text("old", encoding="utf-8")

    writer = TraceWriter.create(trace_root)

    assert writer.root == trace_root.resolve()
    assert not (trace_root / "stale.txt").exists()
    assert (trace_root / "manifest.json").exists()
    with pytest.raises(ValueError, match="unsafe trace directory"):
        TraceWriter.create(Path.cwd())


def test_create_preserves_unowned_nonempty_directory(tmp_path: Path) -> None:
    unowned = tmp_path / "dataset"
    unowned.mkdir()
    marker = unowned / "important.csv"
    marker.write_text("data", encoding="utf-8")

    with pytest.raises(ValueError, match="not owned"):
        TraceWriter.create(unowned)

    assert marker.read_text(encoding="utf-8") == "data"


def test_trace_root_safety_rejects_broad_directories(tmp_path: Path) -> None:
    assert is_unsafe_trace_root(Path.cwd())
    assert is_unsafe_trace_root(Path.cwd().parent)
    assert is_unsafe_trace_root(Path.home())
    assert not is_unsafe_trace_root(tmp_path / "trace")


def test_create_refuses_symlink_instead_of_deleting_its_target(tmp_path: Path) -> None:
    target = tmp_path / "valuable"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    link = tmp_path / "trace"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        TraceWriter.create(link)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_artifact_path_cannot_escape_stage_directory(tmp_path: Path) -> None:
    writer = TraceWriter.create(tmp_path / "trace")

    for name in ("../../outside.json", str(tmp_path / "absolute.json")):
        with pytest.raises(ValueError, match="unsafe trace artifact"):
            writer.write_json("12_evaluation", name, {"secret": False})

    assert not (tmp_path / "outside.json").exists()
    assert not (tmp_path / "absolute.json").exists()


def test_stage_context_records_the_actual_failed_stage(tmp_path: Path) -> None:
    writer = TraceWriter.create(tmp_path / "trace")

    with pytest.raises(RuntimeError, match="ledger broke"), writer.stage(
        "02_ledger_loaded"
    ):
        writer.write_json("02_ledger_loaded", "partial.json", {"rows": 1})
        raise RuntimeError("ledger broke")

    manifest = json.loads((writer.root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stages"][0]["name"] == "02_ledger_loaded"
    assert manifest["stages"][0]["status"] == "failed"
    assert manifest["stages"][0]["artifacts"] == ["02_ledger_loaded/partial.json"]
    assert manifest["stages"][0]["error"] == {
        "type": "RuntimeError", "message": "ledger broke"
    }
    assert manifest["stages"][0]["duration_seconds"] > 0


def test_successful_stage_records_duration(tmp_path: Path) -> None:
    writer = TraceWriter.create(tmp_path / "trace")

    with writer.stage("01_template"):
        time.sleep(0.001)

    manifest = json.loads((writer.root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stages"][0]["status"] == "completed"
    assert manifest["stages"][0]["duration_seconds"] > 0


def test_json_csv_and_manifest_are_human_readable_and_lossless(tmp_path: Path) -> None:
    writer = TraceWriter.create(tmp_path / "trace")

    json_path = writer.write_json(
        "01_template",
        "values.json",
        {
            "amount": Decimal("12.3400"),
            "day": date(2025, 7, 1),
            "category": Category.REVENUE,
            "source": Path("data/input.pdf"),
            "model": SampleModel(label="ratio", value=Decimal("1.250")),
        },
    )
    csv_path = writer.write_csv(
        "02_ledger_loaded",
        "ledger.csv",
        [{"txn_id": "TXN-P1-1", "amount": Decimal("100.00")}],
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload == {
        "amount": "12.3400",
        "day": "2025-07-01",
        "category": "revenue",
        "source": "data/input.pdf",
        "model": {"label": "ratio", "value": "1.250"},
    }
    assert csv_path.read_text(encoding="utf-8") == "txn_id,amount\nTXN-P1-1,100.00\n"

    manifest = json.loads((writer.root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fulltrace"] is True
    assert [stage["name"] for stage in manifest["stages"]] == [
        "01_template",
        "02_ledger_loaded",
    ]
    assert manifest["stages"][0]["artifacts"] == ["01_template/values.json"]
