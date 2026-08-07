"""Command-line entry point for the complete covenant pipeline."""

from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from pathlib import Path

from .run import solve, to_submission
from .tracing import TraceWriter
from .tracing.submission import trace_submission


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Halyk covenant solver")
    parser.add_argument("--data-dir", type=Path, default=None, help="dataset root (default: auto)")
    parser.add_argument("--output", type=Path, default=Path("submission.json"))
    parser.add_argument("--team", default="cloud1")
    parser.add_argument("--contact-email", default="team@example.com")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--no-llm", dest="use_llm", action="store_false")
    parser.add_argument("--fulltrace", action="store_true", help="recreate a complete trace")
    parser.add_argument("--trace-dir", type=Path, default=Path("trace"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.data_dir
    if root is None:
        from .paths import data_dir

        root = data_dir()
    trace = None
    if args.fulltrace:
        trace_root = args.trace_dir.expanduser().resolve()
        data_root = root.expanduser().resolve()
        output = args.output.expanduser().resolve()
        if (
            trace_root in (data_root, output)
            or trace_root in data_root.parents
            or data_root in trace_root.parents
            or trace_root in output.parents
            or output in trace_root.parents
        ):
            raise ValueError("trace directory must not overlap data-dir or output")
        trace = TraceWriter.create(args.trace_dir)

    report = solve(data_dir=root, use_llm=args.use_llm, trace=trace)
    submission_stage = trace.stage("13_submission") if trace is not None else nullcontext()
    with submission_stage:
        submission = to_submission(
            report,
            root / "submission_template.json",
            team=args.team,
            contact_email=args.contact_email,
            model=args.model,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(submission, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if trace is not None:
            trace_submission(trace, submission)

    print(
        f"written: {args.output} | scenarios={report.scenarios} "
        f"rules={report.rules_found}/{report.cells_expected}"
    )
    if trace is not None:
        print(f"full trace: {trace.root}")
    return 0
