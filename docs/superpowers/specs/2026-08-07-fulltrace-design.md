# Full Pipeline Trace Design

## Goal

Add an opt-in `--fulltrace` mode that makes every material pipeline transformation inspectable by a human. A traced run recreates `trace/`; an ordinary run neither creates nor changes it.

## Architecture

The calculation modules remain independent from trace files. `halyk.tracing.TraceWriter` owns directory lifecycle, safe serialization, ordered stage directories, and `manifest.json`. Focused modules under `halyk/tracing/` serialize the outputs of template loading, ledger parsing and categorization, PyMuPDF extraction, document classification and selection, audit/FX adjustment, related-party resolution, rule extraction, formula extraction, evaluation/evidence, and submission generation.

`solve()` accepts an optional writer. With no writer its behavior and public API remain unchanged. The CLI creates a writer only for `--fulltrace` and passes it through orchestration. Evaluation may expose a filesystem-agnostic calculation explanation, but it must not import or depend on tracing code.

## Output Contract

Each traced run recreates the configured trace root and writes:

```text
trace/
├── manifest.json
├── 01_template/
├── 02_ledger_loaded/
├── 03_ledger_categorized/
├── 04_pymupdf/
├── 05_documents_classified/
├── 06_account_mapping/
├── 07_documents_selected/
├── 08_audit_and_fx/
├── 09_related_parties/
├── 10_rules/
├── 11_formulas/
├── 12_evaluation/
└── 13_submission/
```

Tables use CSV plus JSON metadata. Each PDF produces `<source-stem>.txt`; `index.json` records source path, pages, kind, edition, account IDs, output path, and read errors. Scenario stages record selected source documents, extracted objects, and ledger snapshots before and after mutations. Each evaluation record includes the rule, optional FormulaSpec, in-scope and basis transactions, aggregate values, threshold/comparator, verdict, and all evidence-removal trials. JSON renders Decimal and date values as strings so no precision is lost.

`manifest.json` records stage order, status, counts, artifacts, and errors. No environment variables, API keys, or credential headers are written.

Recursive recreation is permitted only for a directory positively identified by its fulltrace ownership marker. A nonempty unowned directory, symlink, broad filesystem root, input overlap, output overlap, or artifact path escaping its stage directory is rejected without deletion.

## CLI and Makefile

`python -m halyk` is the supported entry point. It accepts input/output/team/model options, `--no-llm`, `--fulltrace`, and `--trace-dir` (default `trace`). The trace root is recreated only after CLI argument parsing succeeds. The implementation rejects dangerous trace targets such as the filesystem root or current project root.

The Makefile exposes normal and traced runs. `make run` invokes the pipeline normally; `make fulltrace` passes `--fulltrace`. `ARGS` remains available for overrides such as `--no-llm`.

## Failure Behavior

A readable PDF failure is retained in the PyMuPDF index instead of disappearing silently. A pipeline exception marks the active manifest stage failed and is re-raised so the CLI exits nonzero. Successfully written earlier artifacts remain available for diagnosis. An ordinary run preserves the existing best-effort calculation behavior.

## Tests

Tests use small temporary fixtures and real filesystem writes. They verify trace recreation and safety, lossless serialization, PDF text artifacts and error indexing, ledger stage artifacts, evaluation explanations/evidence trials, `solve()` integration without an LLM, CLI opt-in behavior, and Makefile invocation. The final check runs the complete test suite, Ruff, and a real deterministic `make fulltrace ARGS=--no-llm` against the included dataset.
