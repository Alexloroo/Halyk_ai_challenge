import json
from collections import Counter
from pathlib import Path

import pandas as pd

from halyk_covenants.benchmark.models import (
    BenchmarkReport,
    CaseBenchmarkResult,
    DataQualityFinding,
    DataQualityProfile,
)
from halyk_covenants.benchmark.scoring import score_answer, summarize_scores
from halyk_covenants.domain import CovenantResult, CovenantSpec
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.synthetic.models import BenchmarkCase, DatasetManifest
from halyk_covenants.synthetic.validation import require_valid_dataset


def run_benchmark(dataset_root: Path) -> BenchmarkReport:
    require_valid_dataset(dataset_root)
    manifest = DatasetManifest.model_validate_json(
        (dataset_root / "manifest.json").read_text(encoding="utf-8")
    )
    cases = [
        BenchmarkCase.model_validate(payload)
        for payload in json.loads(
            (dataset_root / "benchmark" / "cases.json").read_text(encoding="utf-8")
        )
    ]
    workbook_path = dataset_root / "transactions" / "synthetic_transactions.xlsx"
    results: list[CaseBenchmarkResult] = []
    covenant_cache: dict[str, CovenantSpec] = {}
    with DuckDBStore() as store:
        store.load_transactions(workbook_path)
        service = EvaluationService(store)
        for case in cases:
            try:
                covenant = covenant_cache.get(case.covenant_id)
                if covenant is None:
                    covenant = CovenantSpec.model_validate_json(
                        (dataset_root / "covenants" / f"{case.covenant_id}.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    covenant_cache[case.covenant_id] = covenant
                actual = service.evaluate(covenant, case.borrower_id, case.evaluation_date)
            except Exception as exc:
                actual = CovenantResult(
                    borrower_id=case.borrower_id,
                    covenant_id=case.covenant_id,
                    verdict="unknown",
                    status="failed",
                    errors=[f"benchmark case execution failed: {exc}"],
                )
            results.append(
                CaseBenchmarkResult(
                    case_id=case.case_id,
                    question=case.question,
                    covenant_id=case.covenant_id,
                    borrower_id=case.borrower_id,
                    evaluation_date=case.evaluation_date,
                    expected=case.expected,
                    actual=actual,
                    score=score_answer(case.case_id, case.expected, actual),
                )
            )

    scores = [result.score for result in results]
    status_counts = dict(sorted(Counter(result.actual.status for result in results).items()))
    failure_stage_counts = dict(
        sorted(
            Counter(
                result.actual.failure_stage.value
                for result in results
                if result.actual.failure_stage is not None
            ).items()
        )
    )
    return BenchmarkReport(
        dataset_version=manifest.dataset_version,
        benchmark_scope="Golden CovenantSpec execution against synthetic XLSX transactions",
        methodology=(
            "Each borrower/covenant pair is evaluated independently through DuckDBStore and "
            "EvaluationService. Number, verdict, and evidence transaction are scored independently."
        ),
        summary=summarize_scores(scores),
        status_counts=status_counts,
        failure_stage_counts=failure_stage_counts,
        data_quality=_profile_workbook(workbook_path),
        known_limitations=manifest.known_limitations,
        cases=results,
    )


def _profile_workbook(path: Path) -> DataQualityProfile:
    frame = pd.read_excel(path, sheet_name="transactions", dtype=str)
    dates = pd.to_datetime(frame["transaction_date"], errors="raise")
    exact_duplicates = int(frame.duplicated().sum())
    duplicated_ids = int(
        frame.loc[frame["transaction_id"].duplicated(keep=False), "transaction_id"].nunique()
    )
    row_count = len(frame)
    duplicate_rate = exact_duplicates / row_count
    findings = [
        DataQualityFinding(
            finding_id="DQ-001",
            severity="medium",
            confidence="high",
            evidence=(
                f"{exact_duplicates} exact duplicate beyond the first occurrence "
                f"({duplicate_rate:.2%} of {row_count} rows)."
            ),
            risk="Aggregate sums and counts include the retained duplicate by design.",
            remediation=(
                "Keep the row for this benchmark; require a source-semantic deduplication policy "
                "before removing duplicates in production."
            ),
        ),
        DataQualityFinding(
            finding_id="DQ-002",
            severity="high",
            confidence="high",
            evidence="The workbook contains both KZT and USD transaction rows.",
            risk="Unfiltered cross-currency aggregation would produce an invalid monetary metric.",
            remediation=(
                "Require covenant currency filters or an explicit approved FX conversion rule."
            ),
        ),
        DataQualityFinding(
            finding_id="DQ-003",
            severity="low",
            confidence="high",
            evidence="Transaction rows are intentionally not ordered by transaction_date.",
            risk="Evidence selection is unreliable if code depends on source row order.",
            remediation="Always order trigger/evidence candidates by date and transaction ID.",
        ),
    ]
    return DataQualityProfile(
        row_count=row_count,
        column_count=len(frame.columns),
        exact_duplicate_rows=exact_duplicates,
        duplicate_transaction_id_values=duplicated_ids,
        null_counts={column: int(frame[column].isna().sum()) for column in frame.columns},
        borrower_ids=sorted(str(value) for value in frame["borrower_id"].dropna().unique()),
        currencies=sorted(str(value) for value in frame["currency"].dropna().unique()),
        minimum_date=dates.min().date(),
        maximum_date=dates.max().date(),
        rows_are_chronologically_sorted=bool(dates.is_monotonic_increasing),
        findings=findings,
    )
