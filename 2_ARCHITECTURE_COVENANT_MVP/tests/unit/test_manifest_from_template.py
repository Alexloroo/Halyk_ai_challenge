"""The submission template is the completeness contract on the real dataset.

Every cell it names must be answered, and a missing cell scores the same as a
wrong one — so the expectation cannot be derived from what the pipeline happened
to find. It is given, and detection is checked against it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_covenants.verification import manifest_from_template

TEMPLATE = Path("data/raw/submission_template.json")


def write_template(tmp_path: Path, answers: dict) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "template.json"
    path.write_text(
        json.dumps({"team": "", "contact_email": "", "model": "", "answers": answers}),
        encoding="utf-8",
    )
    return path


def test_every_cell_becomes_a_required_entry(tmp_path):
    path = write_template(
        tmp_path,
        {"P1": {"6.1": {}, "6.2": {}}, "B4": {"6.3": {}}},
    )
    manifest = manifest_from_template(path)

    assert manifest.expected_pairs == {("P1", "6.1"), ("P1", "6.2"), ("B4", "6.3")}
    assert manifest.required_pairs == manifest.expected_pairs, (
        "with this source there is no optional cell"
    )
    assert {e.source for e in manifest.entries} == {"submission_template"}


def test_clause_order_and_scenario_order_do_not_matter(tmp_path):
    a = manifest_from_template(write_template(tmp_path / "a", {"P1": {"6.1": {}, "6.2": {}}}))
    b = manifest_from_template(write_template(tmp_path / "b", {"P1": {"6.2": {}, "6.1": {}}}))
    assert a.expected_pairs == b.expected_pairs


@pytest.mark.parametrize("payload", [{}, {"answers": {}}, {"answers": None}, {"team": "x"}])
def test_a_template_without_cells_is_refused(tmp_path, payload):
    """Silently returning an empty manifest would disable the completeness check."""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="no answers block"):
        manifest_from_template(path)


@pytest.mark.skipif(not TEMPLATE.exists(), reason="real dataset not present")
def test_real_template_yields_thirty_six_cells():
    manifest = manifest_from_template(TEMPLATE)

    assert len(manifest.entries) == 36
    scenarios = {e.borrower_id for e in manifest.entries}
    assert len(scenarios) == 12
    assert scenarios == {
        "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "B1", "B4",
    }
    clauses = {e.covenant_id for e in manifest.entries}
    assert clauses == {"6.1", "6.2", "6.3"}
    assert manifest.required_pairs == manifest.expected_pairs


@pytest.mark.skipif(not TEMPLATE.exists(), reason="real dataset not present")
def test_manifest_matches_the_answer_key_cell_for_cell():
    """Template and ground truth must describe the same 36 cells."""
    truth = json.loads(Path("data/raw/ground_truth.json").read_text(encoding="utf-8"))
    key_pairs = {
        (scenario, clause)
        for scenario, payload in truth["scenarios"].items()
        for clause in payload["covenants"]
    }
    assert manifest_from_template(TEMPLATE).expected_pairs == key_pairs
