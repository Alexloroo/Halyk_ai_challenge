import json
from decimal import Decimal
from pathlib import Path

from halyk_covenants.benchmark.reporting import write_benchmark_reports
from halyk_covenants.benchmark.runner import run_benchmark
from halyk_covenants.synthetic.generator import generate_synthetic_dataset


def test_benchmark_awards_all_independent_components_after_trigger_selection(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "synthetic"
    generate_synthetic_dataset(dataset)

    report = run_benchmark(dataset)

    assert report.summary.total_cases == 10
    assert report.summary.earned_components == 30
    assert report.summary.maximum_components == 30
    assert report.summary.number_accuracy == Decimal("1")
    assert report.summary.verdict_accuracy == Decimal("1")
    assert report.summary.evidence_accuracy == Decimal("1")
    assert report.summary.component_accuracy == Decimal("1")
    assert report.summary.full_exact_match_accuracy == Decimal("1")
    assert report.summary.failed_case_ids == []

    trigger = next(case for case in report.cases if case.case_id == "ALPHA-COUNT-TRIGGER")
    assert trigger.actual.number == 3
    assert trigger.actual.verdict == "violated"
    assert trigger.actual.evidence_transaction_id == "A003"
    assert trigger.actual.status == "success"
    assert trigger.score.component_score == 3
    assert trigger.score.status_match is True


def test_benchmark_profiles_deliberate_data_quality_risks(tmp_path: Path) -> None:
    dataset = tmp_path / "synthetic"
    generate_synthetic_dataset(dataset)

    report = run_benchmark(dataset)
    profile = report.data_quality

    assert profile.row_count == 14
    assert profile.column_count == 11
    assert profile.exact_duplicate_rows == 1
    assert profile.duplicate_transaction_id_values == 1
    assert profile.borrower_ids == ["000777", "B001", "B002"]
    assert profile.currencies == ["KZT", "USD"]
    assert profile.minimum_date.isoformat() == "2026-04-01"
    assert profile.maximum_date.isoformat() == "2026-05-01"
    assert profile.rows_are_chronologically_sorted is False
    assert {finding.severity for finding in profile.findings} == {"medium", "low", "high"}
    assert all(finding.confidence == "high" for finding in profile.findings)


def test_json_and_markdown_reports_are_deterministic_and_recomputable(tmp_path: Path) -> None:
    dataset = tmp_path / "synthetic"
    generate_synthetic_dataset(dataset)
    first_report = run_benchmark(dataset)

    json_path, markdown_path = write_benchmark_reports(first_report, dataset / "benchmark")
    first_json = json_path.read_bytes()
    first_markdown = markdown_path.read_bytes()
    second_report = run_benchmark(dataset)
    write_benchmark_reports(second_report, dataset / "benchmark")

    assert json_path.read_bytes() == first_json
    assert markdown_path.read_bytes() == first_markdown
    payload = json.loads(first_json)
    earned = sum(case["score"]["component_score"] for case in payload["cases"])
    assert earned == payload["summary"]["earned_components"] == 30
    assert "ALPHA-COUNT-TRIGGER" in markdown_path.read_text(encoding="utf-8")
    assert "PDF extraction" in markdown_path.read_text(encoding="utf-8")
    assert "100.00%" in markdown_path.read_text(encoding="utf-8")
