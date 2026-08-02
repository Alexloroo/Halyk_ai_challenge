from pathlib import Path

from halyk_covenants.synthetic.definitions import build_synthetic_definition
from halyk_covenants.synthetic.full_pipeline import run_full_synthetic_pipeline


class FixtureRunnable:
    def __init__(self) -> None:
        definition = build_synthetic_definition()
        self.specs = {spec.covenant_id: spec for spec in definition.covenants}

    def invoke(self, messages: list[object]) -> dict[str, object]:
        text = "\n".join(str(getattr(message, "content", message)) for message in messages)
        if "CLAUSE:\n" in text:
            text = text.split("CLAUSE:\n", 1)[1].split("\n\nRELEVANT_CONTEXT:", 1)[0]
        matches = [
            spec.model_dump(mode="json")
            for covenant_id, spec in sorted(self.specs.items())
            if covenant_id in text
        ]
        return {"specs": matches}


class FixtureCompilerModel:
    def __init__(self) -> None:
        self.runnable = FixtureRunnable()

    def with_structured_output(self, schema: type[object], *, method: str) -> FixtureRunnable:
        del schema
        assert method == "json_mode"
        return self.runnable


def expected_submission() -> dict[str, object]:
    rows = [
        ("000777", "COV-GAMMA-SUM", "VIOLATED", "7000000", None),
        ("B001", "COV-ALPHA-COUNT", "VIOLATED", "3", "A003"),
        ("B001", "COV-ALPHA-MAX", "VIOLATED", "6000000", "A002"),
        ("B001", "COV-ALPHA-MIN", "COMPLIED", "2000000", None),
        ("B001", "COV-ALPHA-SUM", "VIOLATED", "16000000", None),
        ("B002", "COV-BETA-AVG", "COMPLIED", "4000000", None),
        ("B002", "COV-BETA-MAX", "COMPLIED", "6000000", None),
        ("B002", "COV-BETA-SUM", "COMPLIED", "12000000", None),
    ]
    return {
        "answers": [
            {
                "borrower_id": borrower,
                "covenant_id": covenant,
                "verdict": verdict,
                "number": number,
                "evidence_transaction_id": evidence,
            }
            for borrower, covenant, verdict, number, evidence in rows
        ]
    }


def test_full_synthetic_pipeline_reaches_exact_valid_submission(tmp_path: Path) -> None:
    report = run_full_synthetic_pipeline(tmp_path, model=FixtureCompilerModel())

    assert report.preprocessing.errors == []
    assert report.preprocessing.failed_compilations == 0
    assert report.preprocessing.compiled_covenants >= 8
    assert report.evaluation.actual_pair_count == report.evaluation.expected_pair_count == 8
    assert report.evaluation.verification.valid is True
    assert report.submission == expected_submission()
