import json
from pathlib import Path

import pytest

from halyk_covenants.review_cli import load_questions


def test_load_questions_maps_borrower_and_covenant(tmp_path: Path) -> None:
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            [
                {
                    "borrower_id": "B001",
                    "covenant_id": "COV-1",
                    "question": "Нарушен ли ковенант по месячному лимиту?",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    questions = load_questions(path)

    assert questions[("B001", "COV-1")] == "Нарушен ли ковенант по месячному лимиту?"


def test_load_questions_rejects_duplicate_pair(tmp_path: Path) -> None:
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            [
                {"borrower_id": "B001", "covenant_id": "COV-1", "question": "Q1"},
                {"borrower_id": "B001", "covenant_id": "COV-1", "question": "Q2"},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate review question"):
        load_questions(path)
