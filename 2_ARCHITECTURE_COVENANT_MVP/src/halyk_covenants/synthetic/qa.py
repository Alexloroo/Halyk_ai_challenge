import json
from pathlib import Path

from halyk_covenants.synthetic.models import BenchmarkCase


def write_qa_artifacts(cases: list[BenchmarkCase], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    jsonl_path = directory / "qa_pairs.jsonl"
    markdown_path = directory / "qa_pairs.md"

    records = [
        {
            "case_id": case.case_id,
            "question": case.question,
            "borrower_id": case.borrower_id,
            "evaluation_date": case.evaluation_date.isoformat(),
            "covenant_id": case.covenant_id,
            "document_file": case.document_file,
            "answer": case.expected.model_dump(mode="json"),
        }
        for case in cases
    ]
    jsonl_path.write_text(
        "".join(
            f"{json.dumps(record, ensure_ascii=False, sort_keys=True)}\n" for record in records
        ),
        encoding="utf-8",
    )

    lines = [
        "# Synthetic Covenant Q&A",
        "",
        "> These answers are derived from golden CovenantSpec files; PDF extraction is not scored.",
        "",
    ]
    for record in records:
        answer = record["answer"]
        lines.extend(
            [
                f"## {record['case_id']}",
                "",
                f"- **Question:** {record['question']}",
                f"- **Borrower:** `{record['borrower_id']}`",
                f"- **Evaluation date:** `{record['evaluation_date']}`",
                f"- **Covenant:** `{record['covenant_id']}`",
                f"- **Source PDF:** `{record['document_file']}`",
                f"- **Verdict:** `{answer['verdict']}`",
                f"- **Number:** `{answer['number']}`",
                f"- **Evidence transaction:** `{answer['evidence_transaction_id']}`",
                f"- **Expected status:** `{answer['status']}`",
                f"- **Explanation:** {answer['explanation']}",
                "",
            ]
        )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return jsonl_path, markdown_path
