from __future__ import annotations

import json
from pathlib import Path

from halyk.tracing import TraceWriter
from halyk.tracing.ground_truth import compare_ground_truth, trace_ground_truth


def test_compare_ground_truth_reports_field_differences_and_case_score() -> None:
    submission = {
        "answers": {
            "P1": {
                "6.1": {
                    "status": "BREACH",
                    "actual": 102.5,
                    "evidence_txn_id": None,
                },
                "6.2": {
                    "status": "COMPLIANT",
                    "actual": 50,
                    "evidence_txn_id": "TXN-WRONG",
                },
            }
        }
    }
    ground_truth = {
        "scenarios": {
            "P1": {
                "covenants": {
                    "6.1": {
                        "status": "BREACH",
                        "actual": 100,
                        "evidence_txn_id": None,
                    },
                    "6.2": {
                        "status": "BREACH",
                        "actual": 50,
                        "evidence_txn_id": "TXN-P1-1",
                    },
                }
            }
        }
    }

    result = compare_ground_truth(submission, ground_truth)

    assert result["summary"] == {
        "status": "compared",
        "cells": 2,
        "status_matches": 1,
        "actual_exact_matches": 1,
        "actual_within_5_percent": 2,
        "evidence_matches": 1,
        "exact_cell_matches": 0,
        "unweighted_score": 0.75,
        "unweighted_max": 2.0,
        "unweighted_percent": 37.5,
    }
    first = result["comparisons"][0]
    assert first["scenario_id"] == "P1"
    assert first["clause"] == "6.1"
    assert first["actual_relative_error"] == 0.025
    assert first["actual_score_factor"] == 0.5
    assert first["cell_score"] == 0.75
    assert result["comparisons"][1]["cell_score"] == 0.0


def test_trace_ground_truth_writes_json_csv_and_manifest_counts(tmp_path: Path) -> None:
    truth_path = tmp_path / "ground_truth.json"
    truth_path.write_text(
        json.dumps(
            {
                "scenarios": {
                    "P1": {
                        "covenants": {
                            "6.1": {
                                "status": "COMPLIANT",
                                "actual": 0,
                                "evidence_txn_id": None,
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    submission = {
        "answers": {
            "P1": {
                "6.1": {
                    "status": "COMPLIANT",
                    "actual": 0,
                    "evidence_txn_id": None,
                }
            }
        }
    }
    writer = TraceWriter.create(tmp_path / "trace")

    with writer.stage("14_ground_truth"):
        trace_ground_truth(writer, submission, truth_path)

    comparison = json.loads(
        (writer.root / "14_ground_truth/comparison.json").read_text(encoding="utf-8")
    )
    assert comparison["summary"]["unweighted_score"] == 1.0
    assert (writer.root / "14_ground_truth/comparison.csv").exists()
    manifest = json.loads((writer.root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stages"][-1]["cells"] == 1
    assert manifest["stages"][-1]["exact_cell_matches"] == 1


def test_missing_and_unexpected_cells_never_count_as_field_matches() -> None:
    result = compare_ground_truth(
        {"answers": {"EXTRA": {"6.1": {"status": "COMPLIANT", "actual": 1}}}},
        {
            "scenarios": {
                "MISSING": {
                    "covenants": {
                        "6.1": {
                            "status": "COMPLIANT",
                            "actual": 1,
                            "evidence_txn_id": None,
                        }
                    }
                }
            }
        },
    )

    assert [row["cell_presence"] for row in result["comparisons"]] == [
        "missing_submission",
        "unexpected",
    ]
    assert result["summary"]["status_matches"] == 0
    assert result["summary"]["evidence_matches"] == 0
    assert result["summary"]["unweighted_score"] == 0.0
