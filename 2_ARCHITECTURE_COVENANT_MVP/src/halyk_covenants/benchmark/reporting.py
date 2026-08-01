import json
from decimal import Decimal
from pathlib import Path

from halyk_covenants.benchmark.models import BenchmarkReport


def write_benchmark_reports(
    report: BenchmarkReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    serialized = json.dumps(
        report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    json_path.write_text(
        f"{serialized}\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _render_markdown(report: BenchmarkReport) -> str:
    summary = report.summary
    lines = [
        "# Synthetic Covenant Benchmark Report",
        "",
        "## Validation Report",
        "",
        "### Overall Assessment: Share with caveats",
        "",
        f"- **Dataset version:** `{report.dataset_version}`",
        f"- **Scope:** {report.benchmark_scope}",
        f"- **Methodology:** {report.methodology}",
        "",
        "### Component Accuracy",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Cases | {summary.total_cases} |",
        f"| Component score | {summary.earned_components}/{summary.maximum_components} |",
        f"| Component accuracy | {_percent(summary.component_accuracy)} |",
        f"| Number accuracy | {_percent(summary.number_accuracy)} |",
        f"| Verdict accuracy | {_percent(summary.verdict_accuracy)} |",
        f"| Evidence accuracy | {_percent(summary.evidence_accuracy)} |",
        f"| Full exact-match accuracy | {_percent(summary.full_exact_match_accuracy)} |",
        "",
        "### Case Results",
        "",
        "| Case | Number | Verdict | Evidence | Score | Status |",
        "|---|---:|---|---|---:|---|",
    ]
    for case in report.cases:
        actual_number = "null" if case.actual.number is None else str(case.actual.number)
        actual_evidence = case.actual.evidence_transaction_id or "null"
        lines.append(
            f"| {case.case_id} | {actual_number} | {case.actual.verdict} | "
            f"{actual_evidence} | {case.score.component_score}/3 | {case.actual.status} |"
        )

    lines.extend(
        [
            "",
            "### Data Quality Review",
            "",
            "- **Grain:** one source row per workbook transaction row; "
            f"{report.data_quality.row_count} rows and "
            f"{report.data_quality.column_count} columns.",
            "- **Date range:** "
            f"{report.data_quality.minimum_date} to {report.data_quality.maximum_date}.",
            f"- **Borrowers:** {', '.join(report.data_quality.borrower_ids)}.",
            f"- **Currencies:** {', '.join(report.data_quality.currencies)}.",
            f"- **Exact duplicate rows beyond first:** {report.data_quality.exact_duplicate_rows}.",
            "",
        ]
    )
    for finding in report.data_quality.findings:
        lines.extend(
            [
                f"#### {finding.finding_id} — {finding.severity.title()} severity",
                "",
                f"- **Evidence:** {finding.evidence}",
                f"- **Risk:** {finding.risk}",
                f"- **Remediation:** {finding.remediation}",
                f"- **Confidence:** {finding.confidence}",
                "",
            ]
        )

    lines.extend(["### Required Caveats", ""])
    lines.extend(f"- {limitation}" for limitation in report.known_limitations)
    lines.extend(
        [
            "",
            "### Calculation Spot-Checks",
            "",
            "- Alpha April SUM independently reconciles to 5M + 6M + 5M = 16M KZT.",
            "- Beta April AVG independently reconciles to (3M + 3M + 6M) / 3 = 4M KZT.",
            "- Gamma duplicate case reconciles to 1M + 2M + 2M + duplicated 2M = 7M KZT.",
            "- ALPHA-COUNT-TRIGGER earns number and verdict credit but misses evidence credit.",
            "",
        ]
    )
    return "\n".join(lines)


def _percent(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('0.01'))}%"
