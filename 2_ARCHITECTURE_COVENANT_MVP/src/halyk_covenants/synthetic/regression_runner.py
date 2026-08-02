from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from halyk_covenants.domain import CovenantResult, CovenantSpec
from halyk_covenants.evals import score_covenant_result
from halyk_covenants.evaluators import EvaluationService, TemporalEvaluationService
from halyk_covenants.storage import DuckDBStore


def run_regression_v2(dataset_root: Path) -> dict[str, Any]:
    """Run deterministic execution against synthetic v2 gold rules.

    This deliberately bypasses OCR and DeepSeek so it can run in ordinary CI while those provider
    integrations remain separately inspectable behind interfaces.
    """
    dataset_root = dataset_root.resolve()
    expected_payload = json.loads(
        (dataset_root / "gold" / "expected_submission.json").read_text(encoding="utf-8")
    )
    specs = [
        CovenantSpec.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((dataset_root / "gold" / "covenants").glob("*.json"))
    ]
    by_group: dict[tuple[str, str], list[CovenantSpec]] = {}
    for spec in specs:
        group_id = spec.covenant_group_id or spec.covenant_id
        for borrower_id in spec.borrower_ids:
            by_group.setdefault((borrower_id, group_id), []).append(spec)

    case_rows: list[dict[str, Any]] = []
    failure_stages: Counter[str] = Counter()
    with DuckDBStore() as store:
        store.load_transactions(dataset_root / "input" / "transactions.csv")
        temporal = TemporalEvaluationService(EvaluationService(store))
        for borrower_entry in expected_payload["results"]:
            borrower_id = str(borrower_entry["borrower_id"])
            for expected_entry in borrower_entry["covenants"]:
                covenant_id = str(expected_entry["covenant_id"])
                versions = by_group[(borrower_id, covenant_id)]
                actual = temporal.evaluate_versions(
                    versions,
                    borrower_id=borrower_id,
                    evaluation_date=date(2026, 4, 30),
                )
                expected = CovenantResult(
                    borrower_id=borrower_id,
                    covenant_id=covenant_id,
                    verdict=expected_entry["verdict"],
                    number=expected_entry["number"],
                    evidence_transaction_id=expected_entry["evidence_transaction_id"],
                    status="success",
                )
                score = score_covenant_result(expected, actual)
                if actual.failure_stage is not None:
                    failure_stages[actual.failure_stage.value] += 1
                case_rows.append(
                    {
                        "borrower_id": borrower_id,
                        "covenant_id": covenant_id,
                        "expected": expected.model_dump(mode="json"),
                        "actual": actual.model_dump(mode="json"),
                        "scores": score,
                    }
                )

    earned = sum(row["scores"]["component_score"] for row in case_rows)
    maximum = len(case_rows) * 3
    failed_cases = [
        f"{row['borrower_id']}:{row['covenant_id']}"
        for row in case_rows
        if not row["scores"]["full_exact_match"]
    ]
    return {
        "dataset_id": expected_payload["dataset_id"],
        "logical_covenants": len(case_rows),
        "earned_components": earned,
        "maximum_components": maximum,
        "component_accuracy": earned / maximum if maximum else 1.0,
        "failure_stage_counts": dict(sorted(failure_stages.items())),
        "failed_cases": failed_cases,
        "cases": case_rows,
    }
