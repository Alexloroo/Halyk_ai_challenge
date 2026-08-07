"""Where the dataset lives. One place, so nothing else guesses."""

from __future__ import annotations

import os
from pathlib import Path

_ENV = "HALYK_DATA_DIR"


def data_dir() -> Path:
    """Root of the dataset. Override with HALYK_DATA_DIR."""
    override = os.environ.get(_ENV)
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "raw"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"dataset not found; set {_ENV} to the directory holding "
        "master_ledger_2025.csv and documents/"
    )


def ledger_csv() -> Path:
    return data_dir() / "master_ledger_2025.csv"


def documents_dir() -> Path:
    return data_dir() / "documents"


def template_json() -> Path:
    return data_dir() / "submission_template.json"


def ground_truth_json() -> Path:
    return data_dir() / "ground_truth.json"
