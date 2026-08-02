from pathlib import Path

from halyk_covenants.synthetic.regression_runner import run_regression_v2
from halyk_covenants.synthetic.regression_v2 import generate_regression_dataset_v2


def test_expanded_regression_v2_scores_all_hackathon_components(tmp_path: Path) -> None:
    root = tmp_path / "regression-v2"
    generate_regression_dataset_v2(root)

    report = run_regression_v2(root)

    assert report["logical_covenants"] == 9
    assert report["maximum_components"] == 27
    assert report["earned_components"] == 27
    assert report["component_accuracy"] == 1.0
    assert report["failed_cases"] == []
