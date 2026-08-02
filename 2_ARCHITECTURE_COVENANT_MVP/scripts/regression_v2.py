from __future__ import annotations

import argparse
import json
from pathlib import Path

from halyk_covenants.synthetic import generate_regression_dataset_v2, run_regression_v2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and run the synthetic covenant v2 deterministic regression suite."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/regression-v2"),
        help="Dataset output directory.",
    )
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Generate the corpus without running deterministic evaluation.",
    )
    args = parser.parse_args()

    manifest = generate_regression_dataset_v2(args.output)
    if args.generate_only:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    report = run_regression_v2(args.output)
    report_path = args.output / "gold" / "regression_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if not report["failed_cases"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
