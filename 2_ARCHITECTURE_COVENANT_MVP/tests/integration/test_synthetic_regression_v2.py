import json
from pathlib import Path

import fitz

from halyk_covenants.synthetic.regression_v2 import generate_regression_dataset_v2


def test_regression_v2_contains_new_hackathon_failure_modes(tmp_path: Path) -> None:
    root = tmp_path / "regression-v2"
    manifest = generate_regression_dataset_v2(root)

    assert manifest["borrowers"] == 4
    assert manifest["covenants"] == 9
    assert (root / "input" / "transactions.csv").exists()
    assert (root / "gold" / "expected_submission.json").exists()
    assert (root / "gold" / "covenants" / "COV-A2-v1.json").exists()
    assert (root / "gold" / "covenants" / "COV-A2-v2.json").exists()

    expected = json.loads((root / "gold" / "expected_submission.json").read_text("utf-8"))
    result_count = sum(len(item["covenants"]) for item in expected["results"])
    assert result_count == 9


def test_beta_scan_is_image_only_and_table_document_is_native(tmp_path: Path) -> None:
    root = tmp_path / "regression-v2"
    generate_regression_dataset_v2(root)

    scan = fitz.open(root / "input" / "documents" / "beta_covenants_scan.pdf")
    table = fitz.open(root / "input" / "documents" / "portfolio_covenants_table.pdf")
    try:
        assert "Beta Logistics" not in "".join(page.get_text() for page in scan)
        assert "Gamma Retail" in "".join(page.get_text() for page in table)
    finally:
        scan.close()
        table.close()
