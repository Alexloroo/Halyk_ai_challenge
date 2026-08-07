"""CLI entry point for running the pipeline via `python -m halyk`."""

from __future__ import annotations

import json
from pathlib import Path
from . import paths
from .run import solve, to_submission


def main() -> None:
    root = paths.data_dir()
    print("=== Halyk Covenant Evaluation Pipeline ===")
    print(f"Dataset directory: {root}")

    report = solve(use_llm=True)

    out_dir = root.parent / "check"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "submission.json"

    template_path = root / "submission_template.json"
    submission = to_submission(report, template_path)

    out_path.write_text(json.dumps(submission, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nPipeline finished successfully!")
    print(f"  Scenarios processed: {report.scenarios}")
    print(f"  Rules extracted:     {report.rules_found}")
    print(f"  Cells evaluated:     {report.cells_expected}")
    print(f"  Submission written:  {out_path}")


if __name__ == "__main__":
    main()
