"""The scorer is the instrument every later step is measured with.

If it is wrong, every subsequent measurement is wrong in the same direction and
nothing downstream can be trusted. These tests pin the scale from CASE.ru.md
section 4 against worked examples.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_covenants.scoring import score_cell, score_submission
from halyk_covenants.scoring.models import (
    ACTUAL_WEIGHT,
    EVIDENCE_WEIGHT,
    STATUS_WEIGHT,
)

GROUND_TRUTH = Path("data/raw/ground_truth.json")


def key(status="BREACH", actual=1000.0, evidence=None):
    return {"status": status, "actual": actual, "evidence_txn_id": evidence}


def answer(status="BREACH", actual=1000.0, evidence=None):
    return {"status": status, "actual": actual, "evidence_txn_id": evidence}


# --- status: all or nothing for the whole cell ------------------------------------


def test_wrong_status_zeroes_the_entire_cell():
    """Even a perfect actual and evidence earn nothing behind a wrong status."""
    cell = score_cell(
        "P1", "6.1",
        answer(status="COMPLIANT", actual=1000.0, evidence="TXN-P1-0001"),
        key(status="BREACH", actual=1000.0, evidence="TXN-P1-0001"),
    )
    assert cell.total == 0.0
    assert cell.status_points == 0.0
    assert cell.actual_points == 0.0
    assert cell.evidence_points == 0.0


@pytest.mark.parametrize("status", ["compliant", "Breach", "BREACHED", "", None, "OK"])
def test_status_must_match_the_vocabulary_exactly(status):
    cell = score_cell("P1", "6.1", answer(status=status), key())
    assert cell.total == 0.0


def test_missing_cell_scores_zero():
    cell = score_cell("P1", "6.1", None, key())
    assert cell.total == 0.0
    assert cell.reason == "cell absent"


# --- actual: linear decay, zero at 5 % --------------------------------------------


@pytest.mark.parametrize(
    ("ours", "expected_fraction"),
    [
        (1000.0, 1.0),      # exact
        (1012.5, 0.75),     # 1.25 % error
        (1025.0, 0.5),      # 2.5 %  -> half, stated explicitly in the case
        (1037.5, 0.25),     # 3.75 %
        (1050.0, 0.0),      # 5 %    -> zero
        (2000.0, 0.0),      # far beyond
        (975.0, 0.5),       # symmetric below the key
    ],
)
def test_actual_decays_linearly_to_zero_at_five_percent(ours, expected_fraction):
    cell = score_cell("P1", "6.1", answer(actual=ours), key(actual=1000.0))
    assert cell.actual_points == pytest.approx(ACTUAL_WEIGHT * expected_fraction)


def test_rounding_error_costs_almost_nothing():
    cell = score_cell("P1", "6.1", answer(actual=1000.01), key(actual=1000.0))
    assert cell.actual_points > ACTUAL_WEIGHT * 0.999


@pytest.mark.parametrize("ours", [None, "1000.00", True])
def test_non_numeric_actual_earns_nothing(ours):
    """A numeric string is not a number: the case calls that non-scorable."""
    cell = score_cell("P1", "6.1", answer(actual=ours), key(actual=1000.0))
    assert cell.actual_points == 0.0
    assert cell.status_points == STATUS_WEIGHT


# --- evidence: exact when keyed, tied to actual when null -------------------------


def test_keyed_evidence_is_exact_match_only():
    k = key(evidence="TXN-P1-0020")
    hit = score_cell("P1", "6.1", answer(evidence="TXN-P1-0020"), k)
    miss = score_cell("P1", "6.1", answer(evidence="TXN-P1-0021"), k)
    none = score_cell("P1", "6.1", answer(evidence=None), k)
    assert hit.evidence_points == EVIDENCE_WEIGHT
    assert miss.evidence_points == 0.0
    assert none.evidence_points == 0.0


def test_keyed_evidence_is_independent_of_actual():
    """A wrong actual must not cost the evidence points when the key is real."""
    cell = score_cell(
        "P1", "6.1",
        answer(actual=99999.0, evidence="TXN-P1-0020"),
        key(actual=1000.0, evidence="TXN-P1-0020"),
    )
    assert cell.actual_points == 0.0
    assert cell.evidence_points == EVIDENCE_WEIGHT


@pytest.mark.parametrize(
    ("ours", "expected_fraction"),
    [(1000.0, 1.0), (1025.0, 0.5), (1050.0, 0.0)],
)
def test_null_key_evidence_decays_with_actual(ours, expected_fraction):
    """The 0.20 is earned, not granted: it rides on the same scale as actual."""
    cell = score_cell("P1", "6.1", answer(actual=ours), key(actual=1000.0, evidence=None))
    assert cell.evidence_points == pytest.approx(EVIDENCE_WEIGHT * expected_fraction)


@pytest.mark.parametrize("guess", [None, "TXN-P1-0001", "anything"])
def test_null_key_ignores_whatever_we_send_for_evidence(guess):
    a = answer(actual=1000.0, evidence=guess)
    cell = score_cell("P1", "6.1", a, key(actual=1000.0, evidence=None))
    assert cell.evidence_points == EVIDENCE_WEIGHT


# --- whole submission -------------------------------------------------------------


def test_perfect_cell_scores_one():
    cell = score_cell(
        "P1", "6.1",
        answer(actual=1000.0, evidence="TXN-P1-0020"),
        key(actual=1000.0, evidence="TXN-P1-0020"),
    )
    assert cell.total == pytest.approx(1.0)


@pytest.mark.skipif(not GROUND_TRUTH.exists(), reason="real dataset not present")
def test_ground_truth_scores_full_marks_against_itself():
    """The instrument must read 36/36 on the answer key. Anything else is a bug."""
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    submission = {
        "answers": {
            scenario: dict(payload["covenants"])
            for scenario, payload in truth["scenarios"].items()
        }
    }
    report = score_submission(submission, truth)

    assert len(report.cells) == 36
    assert report.maximum == 36
    assert report.total == pytest.approx(36.0)
    assert report.missing_cells == []
    assert report.extra_cells == []
    assert report.status_accuracy == 1.0
    assert report.actual_within_tolerance == 1.0
    assert report.evidence_exact == 1.0


@pytest.mark.skipif(not GROUND_TRUTH.exists(), reason="real dataset not present")
def test_empty_submission_scores_zero_and_reports_every_cell_missing():
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    report = score_submission({"answers": {}}, truth)

    assert report.total == 0.0
    assert len(report.missing_cells) == 36


@pytest.mark.skipif(not GROUND_TRUTH.exists(), reason="real dataset not present")
def test_extra_cells_are_reported_and_earn_nothing():
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    submission = {"answers": {"ZZ": {"9.9": answer()}}}
    report = score_submission(submission, truth)

    assert report.extra_cells == ["ZZ/9.9"]
    assert report.total == 0.0
