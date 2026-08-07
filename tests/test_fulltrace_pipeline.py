from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from halyk.docs import DocKind, Document, Edition
from halyk.run import solve
from halyk.tracing import TraceWriter


def _dataset(root: Path) -> None:
    (root / "documents").mkdir(parents=True)
    (root / "submission_template.json").write_text(
        json.dumps({"answers": {"P1": {"6.1": {}}}}),
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


def test_solve_fulltrace_writes_every_pipeline_stage(tmp_path: Path) -> None:
    data_dir = tmp_path / "raw"
    _dataset(data_dir)
    writer = TraceWriter.create(tmp_path / "trace")

    report = solve(data_dir=data_dir, use_llm=False, trace=writer)

    assert report.scenarios == 1
    expected_stages = [
        "01_template",
        "02_ledger_loaded",
        "03_ledger_categorized",
        "04_pymupdf",
        "05_documents_classified",
        "06_account_mapping",
        "07_documents_selected",
        "08_audit_and_fx",
        "09_related_parties",
        "10_rules",
        "11_formulas",
        "12_evaluation",
    ]
    manifest = json.loads((writer.root / "manifest.json").read_text(encoding="utf-8"))
    assert [stage["name"] for stage in manifest["stages"]] == expected_stages
    assert (writer.root / "04_pymupdf" / "one.txt").read_text(encoding="utf-8").strip()
    categorized = (writer.root / "03_ledger_categorized" / "ledger.csv").read_text(
        encoding="utf-8"
    )
    assert "revenue" in categorized
    assert (writer.root / "08_audit_and_fx" / "P1" / "ledger_before.csv").exists()
    assert (writer.root / "08_audit_and_fx" / "P1" / "ledger_after.csv").exists()
    evaluation = json.loads(
        (writer.root / "12_evaluation" / "P1" / "6_1.json").read_text(encoding="utf-8")
    )
    assert evaluation["answer"]["note"] == "no rule extracted"


def test_solve_marks_the_business_stage_that_failed(tmp_path: Path) -> None:
    data_dir = tmp_path / "raw"
    data_dir.mkdir()
    (data_dir / "submission_template.json").write_text(
        json.dumps({"answers": {"P1": {"6.1": {}}}}),
        encoding="utf-8",
    )
    writer = TraceWriter.create(tmp_path / "trace")

    with pytest.raises(FileNotFoundError):
        solve(data_dir=data_dir, use_llm=False, trace=writer)

    manifest = json.loads((writer.root / "manifest.json").read_text(encoding="utf-8"))
    assert [stage["name"] for stage in manifest["stages"]] == [
        "01_template",
        "02_ledger_loaded",
    ]
    assert manifest["stages"][0]["status"] == "completed"
    assert manifest["stages"][1]["status"] == "failed"
    assert manifest["stages"][1]["error"]["type"] == "FileNotFoundError"


def test_fulltrace_records_real_adjustment_rule_and_evidence_calculation(tmp_path: Path) -> None:
    data_dir = tmp_path / "raw"
    data_dir.mkdir()
    (data_dir / "submission_template.json").write_text(
        json.dumps({"answers": {"P1": {"6.1": {}}}}),
        encoding="utf-8",
    )
    (data_dir / "master_ledger_2025.csv").write_text(
        "txn_id,date,account_id,counterparty,description,amount,currency\n"
        "TXN-P1-0001,2025-02-01,ACC-0001,Vendor LLC,general service,-60.00,USD\n",
        encoding="utf-8",
    )
    documents = [
        Document(
            path=Path("agreement.pdf"),
            text=(
                "ДОГОВОР БАНКОВСКОГО ЗАЙМА ACC-0001\n"
                "Статья 6 Финансовые ковенанты\n"
                "Пункт 6.1 Максимальные расходы по категории.\n"
                "За период с 2025-01-01 по 2025-12-31 капитальные затраты "
                "не должны превышать $50.00.\nСтатья 7"
            ),
            kind=DocKind.CREDIT_AGREEMENT,
            edition=Edition.CURRENT,
            account_ids=["ACC-0001"],
            pages=1,
        ),
        Document(
            path=Path("audit.pdf"),
            text=(
                "ACC-0001 Операция TXN-P1-0001, первоначально учтённая как "
                "операционные расходы, переклассифицирована аудитором как "
                "капитальные затраты."
            ),
            kind=DocKind.AUDIT_NOTES,
            edition=Edition.CURRENT,
            account_ids=["ACC-0001"],
            pages=1,
        ),
        Document(
            path=Path("kyc.pdf"),
            text="KYC-ACC ACC-0001\nVendor LLC\n30.0%\nвладеет 20.0% и более",
            kind=DocKind.KYC,
            edition=Edition.CURRENT,
            account_ids=["ACC-0001"],
            pages=1,
        ),
    ]
    writer = TraceWriter.create(tmp_path / "trace")

    report = solve(data_dir=data_dir, documents=documents, use_llm=False, trace=writer)

    assert report.answers["P1"]["6.1"].status == "BREACH"
    assert report.answers["P1"]["6.1"].evidence_txn_id == "TXN-P1-0001"
    before = (writer.root / "08_audit_and_fx/P1/ledger_before.csv").read_text()
    after = (writer.root / "08_audit_and_fx/P1/ledger_after.csv").read_text()
    assert ",opex," in before
    assert ",capex," in after
    evaluation = json.loads(
        (writer.root / "12_evaluation/P1/6_1.json").read_text(encoding="utf-8")
    )
    assert evaluation["rule"]["kind"] == "max_category_spend"
    assert evaluation["calculation"]["aggregates"] == {"selected_total": "60.00"}
    assert evaluation["evidence_trials"] == {"TXN-P1-0001": "COMPLIANT"}
