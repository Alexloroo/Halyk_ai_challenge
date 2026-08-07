# Full Pipeline Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a human-readable, opt-in full trace for every covenant pipeline stage and expose it through the CLI and Makefile.

**Architecture:** A filesystem-focused `TraceWriter` and focused stage serializers receive orchestration data from `solve()`. Core evaluation exposes filesystem-independent calculation details, while CLI construction determines whether tracing exists at all.

**Tech Stack:** Python 3.12, dataclasses, pathlib, csv/json, argparse, pytest, Ruff, GNU Make.

## Global Constraints

- `--fulltrace` recreates `trace/`; a normal run does not touch it.
- Every material pipeline transformation produces a human-readable artifact.
- Decimal and date values are serialized without precision loss.
- Credentials and environment variables are never traced.
- The current `solve()` API remains compatible when tracing is disabled.

---

### Task 1: Trace writer and stage serializers

**Files:**
- Create: `src/halyk/tracing/__init__.py`
- Create: `src/halyk/tracing/writer.py`
- Create: `src/halyk/tracing/template.py`
- Create: `src/halyk/tracing/ledger.py`
- Create: `src/halyk/tracing/documents.py`
- Create: `src/halyk/tracing/scenario.py`
- Create: `src/halyk/tracing/formulas.py`
- Create: `src/halyk/tracing/evaluation.py`
- Create: `src/halyk/tracing/submission.py`
- Test: `tests/test_tracing.py`

**Interfaces:**
- Produces: `TraceWriter.create(root: Path)`, `write_json(stage, name, payload)`, `write_text(stage, name, text)`, `write_csv(stage, name, rows)`, and focused `trace_*` functions.

- [ ] Write tests proving recreation, dangerous-root rejection, Decimal/date/enum conversion, CSV output, and manifest artifact registration.
- [ ] Run `python -m pytest tests/test_tracing.py -q` and confirm failure because `halyk.tracing` is missing.
- [ ] Implement the writer and focused serializers with deterministic file names and relative manifest paths.
- [ ] Re-run the test and confirm it passes.

### Task 2: Explainable evaluation and PDF diagnostics

**Files:**
- Modify: `src/halyk/evaluate.py`
- Modify: `src/halyk/docs.py`
- Test: `tests/test_evaluation_trace.py`
- Test: `tests/test_document_trace.py`

**Interfaces:**
- Produces: `EvaluationTrace` populated by `evaluate(..., trace=details)` and `find_evidence(..., trials=mapping)`.
- Produces: `DocumentLoadIssue` and `load_documents(directory, issues=issues)`.

- [ ] Write tests asserting period scope, aggregate values, basis IDs, evidence-removal statuses, extracted PDF text, and failed-PDF diagnostics.
- [ ] Run both tests and confirm the new interfaces fail before implementation.
- [ ] Add optional diagnostic objects without changing default return values or calculation results.
- [ ] Re-run both tests and the existing suite.

### Task 3: Pipeline integration

**Files:**
- Modify: `src/halyk/run.py`
- Test: `tests/test_fulltrace_pipeline.py`

**Interfaces:**
- Consumes: `TraceWriter`, focused serializers, `EvaluationTrace`, and document issues.
- Produces: `solve(..., trace: TraceWriter | None = None)` with stages `01` through `12`.

- [ ] Write a fixture-based integration test that runs without LLM and asserts representative artifacts from every stage.
- [ ] Run the test and confirm failure because `solve()` has no `trace` parameter.
- [ ] Wire trace calls at every orchestration boundary, including before/after mutated ledger state and evaluation trials.
- [ ] Re-run the integration test and suite.

### Task 4: CLI, submission trace, and Makefile

**Files:**
- Create: `src/halyk/cli.py`
- Create: `src/halyk/__main__.py`
- Create: `Makefile`
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`, `python -m halyk`, `make run`, and `make fulltrace`.

- [ ] Write CLI tests proving normal runs preserve a sentinel trace directory while `--fulltrace` recreates it and writes stage `13_submission`.
- [ ] Run tests and confirm failure because the CLI is missing.
- [ ] Implement argparse, JSON output, console-script metadata, Make targets, ignore rule, and usage documentation.
- [ ] Re-run CLI tests and suite.

### Task 5: Final verification

**Files:**
- Modify only files required by verification findings.

- [ ] Run `python -m pytest -q` and require zero failures.
- [ ] Run `python -m ruff check .` and require zero errors.
- [ ] Run `make fulltrace ARGS=--no-llm` against `data/raw`.
- [ ] Inspect `trace/manifest.json`, one PyMuPDF text file, categorized CSV, one scenario audit record, one rule, one evaluation, and final submission.
- [ ] Run `git diff --check` and review `git status --short` without touching unrelated user changes.
