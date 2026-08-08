from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from halyk.categorize import Category
from halyk.docs import DocKind, Document, Edition
from halyk.evaluate import EvaluationTrace
from halyk.ledger import LedgerEntry
from halyk.llm_categorize import (
    CategoryResolutionResult,
    FlowDirection,
    TransactionCategorySpec,
)
from halyk.llm_documents import (
    DocumentClassificationResult,
    DocumentClassificationSpec,
)
from halyk.quality import assess_private_readiness
from halyk.rules import Rule, RuleKind
from halyk.run import solve
from halyk.tracing import TraceWriter


def _entry(scenario: str, account: str, txn_id: str, day: date) -> LedgerEntry:
    return LedgerEntry(
        txn_id=txn_id,
        scenario_id=scenario,
        day=day,
        account_id=account,
        counterparty="Customer LLP",
        description="service revenue",
        amount=Decimal("600"),
        currency="USD",
        category=Category.REVENUE,
    )


def test_quality_gate_reports_formula_and_denominator_failures_once() -> None:
    entry = _entry("S1", "ACC-123456", "TXN-S1-0001", date(2026, 6, 1))
    agreement = Document(
        Path("agreement.pdf"),
        "agreement",
        DocKind.CREDIT_AGREEMENT,
        Edition.CURRENT,
        ["ACC-123456"],
        1,
    )
    rule = Rule(
        scenario_id="S1",
        clause="6.1",
        heading="ratio",
        text="capex / revenue <= 1x",
        kind=RuleKind.RATIO,
        comparator="<=",
        threshold=Decimal("1"),
        period=(date(2026, 1, 1), date(2026, 12, 31)),
    )
    details = EvaluationTrace(quality_flags=["missing_formula", "zero_denominator"])

    report = assess_private_readiness(
        template={"S1": ["6.1"]},
        grouped={"S1": [entry]},
        agreements={"S1": agreement},
        parties={"S1": None},
        rules={"S1": {"6.1": rule}},
        formulas={},
        evaluations={"S1/6.1": details},
        categorization_records=[],
        document_issues=[],
        llm_enabled=True,
    )

    assert report.status == "FAIL"
    assert [finding.code for finding in report.findings].count("missing_formula") == 1
    assert {finding.code for finding in report.findings} >= {"missing_formula", "zero_denominator"}


def _write_dataset(root: Path, scenario: str, account: str, txn_rows: list[str]) -> None:
    root.mkdir()
    (root / "submission_template.json").write_text(
        json.dumps({"answers": {scenario: {"6.1": {}}}}), encoding="utf-8"
    )
    (root / "master_ledger_2025.csv").write_text(
        "txn_id,date,account_id,counterparty,description,amount,currency\n"
        + "\n".join(txn_rows)
        + "\n",
        encoding="utf-8",
    )


def test_private_like_identifier_format_and_row_order_changes_preserve_answer(
    tmp_path: Path,
) -> None:
    base_root = tmp_path / "base"
    variant_root = tmp_path / "variant"
    _write_dataset(
        base_root,
        "BASE",
        "ACC-1234",
        [
            "TXN-BASE-0001,2025-04-01,ACC-1234,Customer LLP,service revenue,400,USD",
            "TXN-BASE-0002,2025-06-01,ACC-1234,Customer LLP,service revenue,200,USD",
        ],
    )
    _write_dataset(
        variant_root,
        "PRIVATE99",
        "ACC-123456",
        [
            "TXN-PRIVATE99-9002,2026-06-01,ACC-123456,Customer LLP,service revenue,200,USD",
            "TXN-PRIVATE99-9001,2026-04-01,ACC-123456,Customer LLP,service revenue,400,USD",
        ],
    )
    base_document = Document(
        Path("base.pdf"),
        "ACC-1234\nПункт 6.1 Минимальная выручка.\n"
        "За период с 2025-01-01 по 2025-12-31 выручка не менее $500.00.\nСтатья 7",
        DocKind.CREDIT_AGREEMENT,
        Edition.CURRENT,
        ["ACC-1234"],
        1,
    )
    variant_document = Document(
        Path("variant.pdf"),
        "ACC-123456\nClause 6 . 1) Minimum revenue.\n"
        "Revenue from 01.01.2026 to 31.12.2026 must be at least 500,00 USD.\nArticle 7",
        DocKind.CREDIT_AGREEMENT,
        Edition.CURRENT,
        ["ACC-123456"],
        1,
    )

    base = solve(data_dir=base_root, documents=[base_document], use_llm=False)
    variant_writer = TraceWriter.create(tmp_path / "variant-trace")
    variant = solve(
        data_dir=variant_root,
        documents=[variant_document],
        use_llm=False,
        trace=variant_writer,
    )

    base_answer = base.answers["BASE"]["6.1"]
    variant_answer = variant.answers["PRIVATE99"]["6.1"]
    assert (variant_answer.status, variant_answer.actual) == (
        base_answer.status,
        base_answer.actual,
    )
    readiness = json.loads(
        (variant_writer.root / "private_readiness.json").read_text(encoding="utf-8")
    )
    assert readiness["checks"]["extracted_rules"] == 1
    assert readiness["status"] == "PASS"


def test_hybrid_category_resolution_is_applied_and_traced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "raw"
    _write_dataset(
        root,
        "N1",
        "ACC-123456",
        ["TXN-N1-0001,2026-06-01,ACC-123456,Machine Vendor,orbital fleet synchronization,-600,USD"],
    )
    agreement = Document(
        Path("agreement.pdf"),
        "ACC-123456\nПункт 6.1 Максимальные расходы по категории.\n"
        "Капитальные затраты за период с 2026-01-01 по 2026-12-31 "
        "не должны превышать $500.00.\nСтатья 7",
        DocKind.CREDIT_AGREEMENT,
        Edition.CURRENT,
        ["ACC-123456"],
        1,
    )

    def fake_resolve(requests):
        return {
            request.key: CategoryResolutionResult(
                resolution=TransactionCategorySpec(
                    category=Category.CAPEX,
                    direction=FlowDirection.OUTFLOW,
                    transaction_nature="equipment_modernization",
                    matched_terms=["fleet synchronization"],
                ),
                attempts=1,
            )
            for request in requests
        }

    monkeypatch.setattr("halyk.run.resolve_categories", fake_resolve)
    writer = TraceWriter.create(tmp_path / "trace")

    report = solve(data_dir=root, documents=[agreement], use_llm=True, trace=writer)

    assert report.answers["N1"]["6.1"].actual == Decimal("600")
    assert report.answers["N1"]["6.1"].status == "BREACH"
    decisions = json.loads(
        (writer.root / "03_ledger_categorized/decisions.json").read_text(encoding="utf-8")
    )
    assert decisions[0]["initial_category"] == "opex"
    assert decisions[0]["final_category"] == "capex"
    assert decisions[0]["llm_requested"] is True


def test_unknown_agreement_is_classified_before_document_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "raw"
    _write_dataset(
        root,
        "N2",
        "ACC-654321",
        ["TXN-N2-0001,2026-06-01,ACC-654321,Customer LLP,service revenue,600,USD"],
    )
    agreement = Document(
        Path("opaque.pdf"),
        "CREDIT AGREEMENT\nEXECUTION COPY\nACC-654321\n"
        "Clause 6.1 Minimum revenue.\n"
        "Revenue from 2026-01-01 to 2026-12-31 must be at least USD 500.\n"
        "Article 7",
        DocKind.UNKNOWN,
        Edition.CURRENT,
        ["ACC-654321"],
        1,
    )
    actionable_audit = Document(
        Path("opaque-audit.pdf"),
        "ACC-654321\nAudit working note: 1 EUR equals $ 1.10.",
        DocKind.UNKNOWN,
        Edition.CURRENT,
        ["ACC-654321"],
        1,
    )

    def fake_document_resolve(requests):
        assert [request.key for request in requests] == ["opaque.pdf"]
        return {
            request.key: DocumentClassificationResult(
                resolution=DocumentClassificationSpec(
                    kind=DocKind.CREDIT_AGREEMENT,
                    edition=Edition.CURRENT,
                    matched_terms=["CREDIT AGREEMENT", "EXECUTION COPY"],
                ),
                attempts=1,
            )
            for request in requests
        }

    monkeypatch.setattr("halyk.run.resolve_document_classifications", fake_document_resolve)
    writer = TraceWriter.create(tmp_path / "trace")

    report = solve(
        data_dir=root,
        documents=[agreement, actionable_audit],
        use_llm=True,
        trace=writer,
    )

    assert report.agreements_missing == []
    assert report.answers["N2"]["6.1"].actual == Decimal("600")
    decisions = json.loads(
        (writer.root / "05_documents_classified/decisions.json").read_text(encoding="utf-8")
    )
    assert decisions[0]["initial_kind"] == "unknown"
    assert decisions[0]["final_kind"] == "credit_agreement"
    assert decisions[0]["llm_requested"] is True
    assert decisions[1]["initial_kind"] == "unknown"
    assert decisions[1]["final_kind"] == "unknown"
    assert decisions[1]["llm_requested"] is False
