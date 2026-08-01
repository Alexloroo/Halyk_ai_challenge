# Dockerized Synthetic Covenant Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the deterministic covenant evaluator in one Docker image and add a reproducible synthetic PDF/XLSX/Q&A benchmark with component-level scoring.

**Architecture:** A synthetic definitions module is the single source of truth for transactions, golden covenant specs, and benchmark cases. Focused renderers create PDFs, XLSX, JSON, JSONL, and Markdown; a validator checks cross-artifact integrity; a benchmark runner executes golden specs through the existing `EvaluationService` and writes deterministic reports. One multi-stage Docker image exposes generation, benchmark, and evaluation commands, while Docker Compose sequences generation before benchmarking.

**Tech Stack:** Python 3.12+, Pydantic 2, DuckDB, Pandas/OpenPyXL, ReportLab, PyMuPDF, Typer, Docker, Docker Compose, pytest, Ruff.

## Global Constraints

- Benchmark golden `CovenantSpec` rules; do not claim PDF parsing, OCR, borrower discovery, or covenant compilation accuracy.
- Use one Docker image and two Compose one-shot services: `generate-synthetic` and `benchmark`.
- Preserve money as `Decimal`/`DECIMAL(38, 6)` and identifiers as strings.
- Generate exactly two synthetic PDFs containing both text and tables, with deliberate defects declared in the manifest.
- Generate one XLSX workbook with `transactions`, `borrowers`, `data_dictionary`, and `known_anomalies` sheets.
- Derive machine-readable JSONL and reviewer-readable Markdown Q&A from the same benchmark case registry.
- Score number, verdict, and evidence independently; do not include status in the competition-style component score.
- Include one expected trigger-evidence miss so the benchmark discloses the current selector limitation rather than manufacturing a perfect score.
- Generation and reports must be deterministic; generated content must contain no real personal or company data.

---

### Task 1: Synthetic definitions and benchmark schemas

**Files:**
- Modify: `pyproject.toml`
- Create: `src/halyk_covenants/synthetic/models.py`
- Create: `src/halyk_covenants/synthetic/definitions.py`
- Create: `src/halyk_covenants/synthetic/__init__.py`
- Create: `src/halyk_covenants/benchmark/models.py`
- Create: `src/halyk_covenants/benchmark/scoring.py`
- Create: `src/halyk_covenants/benchmark/__init__.py`
- Test: `tests/unit/test_synthetic_definitions.py`
- Test: `tests/unit/test_benchmark_scoring.py`

**Interfaces:**
- Produces: `SyntheticDatasetDefinition`, `BenchmarkCase`, `ExpectedAnswer`, `CaseScore`, `BenchmarkSummary`, `build_synthetic_definition()`, and `score_answer(expected, actual)`.
- Consumes: existing `CovenantSpec` and `CovenantResult`.

- [ ] **Step 1: Add ReportLab and PyMuPDF dependencies and write failing definition tests**

```python
def test_definition_has_two_documents_four_workbook_sheets_and_all_metrics():
    definition = build_synthetic_definition()
    assert len(definition.documents) == 2
    assert {"sum", "count", "max", "min", "avg"} <= {
        spec.metric.metric_type for spec in definition.covenants
    }
    assert len(definition.cases) == 10
```

- [ ] **Step 2: Run `pytest tests/unit/test_synthetic_definitions.py -q` and verify RED because the package is absent**
- [ ] **Step 3: Implement typed definitions with 10 cases, 8 golden covenants, synthetic borrowers, and transactions**
- [ ] **Step 4: Verify definition IDs and references are unique and every expected value is hand-derived**
- [ ] **Step 5: Write failing scoring tests for Decimal equality, integer counts, null values, evidence misses, and aggregate percentages**
- [ ] **Step 6: Implement independent component scoring and verify GREEN**
- [ ] **Step 7: Commit `feat: define synthetic covenant benchmark`**

### Task 2: XLSX and PDF artifact renderers

**Files:**
- Create: `src/halyk_covenants/synthetic/fonts.py`
- Create: `src/halyk_covenants/synthetic/workbook.py`
- Create: `src/halyk_covenants/synthetic/pdf.py`
- Test: `tests/integration/test_synthetic_renderers.py`

**Interfaces:**
- Consumes: `SyntheticDatasetDefinition`.
- Produces: `render_workbook(definition, path) -> Path`, `render_pdfs(definition, directory) -> list[Path]`, and `register_cyrillic_fonts() -> FontFamily`.

- [ ] **Step 1: Write a failing workbook test that opens the real XLSX and verifies sheet names, text IDs, numeric amount cells, headers, and anomaly rows**
- [ ] **Step 2: Implement styled workbook rendering with frozen headers, filters, widths, number formats, and text identifier formats**
- [ ] **Step 3: Verify workbook GREEN, then write failing PDF tests using PyMuPDF to assert two valid documents, text markers, tables, page counts, and Cyrillic extraction**
- [ ] **Step 4: Implement DejaVu font discovery plus native-text ReportLab PDFs with the approved defects, page headers, tables, footnotes, and provenance markers**
- [ ] **Step 5: Run renderer tests and commit `feat: render synthetic PDF and XLSX fixtures`**

### Task 3: Dataset generation, Q&A, manifest, and validation

**Files:**
- Create: `src/halyk_covenants/synthetic/qa.py`
- Create: `src/halyk_covenants/synthetic/validation.py`
- Create: `src/halyk_covenants/synthetic/generator.py`
- Test: `tests/integration/test_synthetic_generator.py`

**Interfaces:**
- Produces: `generate_synthetic_dataset(output_dir) -> DatasetManifest`, `validate_dataset(root) -> ValidationReport`, and SHA-256 artifact entries.
- Consumes: Task 1 definitions and Task 2 renderers.

- [ ] **Step 1: Write a failing end-to-end generator test for the exact directory topology and artifact count**
- [ ] **Step 2: Implement golden CovenantSpec JSON, cases JSON, Q&A JSONL/Markdown, workbook, PDFs, and deterministic manifest generation in a staging directory**
- [ ] **Step 3: Write failing cross-reference tests for covenant files, borrower IDs, source PDFs, Q&A answers, workbook sheets, and hashes**
- [ ] **Step 4: Implement validation and safe target replacement only after validation passes**
- [ ] **Step 5: Add a failure-path test proving an invalid staged dataset does not replace a valid target**
- [ ] **Step 6: Verify deterministic hashes across two generated directories and commit `feat: generate validated synthetic dataset`**

### Task 4: Benchmark runner and reports

**Files:**
- Create: `src/halyk_covenants/benchmark/runner.py`
- Create: `src/halyk_covenants/benchmark/reporting.py`
- Test: `tests/integration/test_benchmark_runner.py`

**Interfaces:**
- Produces: `run_benchmark(dataset_root) -> BenchmarkReport` and `write_benchmark_reports(report, output_dir) -> tuple[Path, Path]`.
- Consumes: generated workbook, cases, golden covenant JSON, `DuckDBStore`, and `EvaluationService`.

- [ ] **Step 1: Write a failing benchmark test expecting 10 cases, 30 possible components, 29 earned components, 100% number accuracy, 100% verdict accuracy, 90% evidence accuracy, and 90% full exact match**
- [ ] **Step 2: Implement one-time workbook ingestion, cached covenant loading, independent case execution, Decimal-safe scoring, and failure isolation**
- [ ] **Step 3: Write failing report tests for deterministic JSON/Markdown, per-case diagnostics, status counts, and the disclosed trigger-evidence limitation**
- [ ] **Step 4: Implement report writers and recomputation checks, then verify two consecutive runs are byte-identical**
- [ ] **Step 5: Apply `analyze-data-quality` and `validate-data` checks to the generated workbook and benchmark methodology; record material caveats in `report.md`**
- [ ] **Step 6: Commit `feat: add component-level synthetic benchmark`**

### Task 5: CLI integration

**Files:**
- Modify: `src/halyk_covenants/cli.py`
- Test: `tests/integration/test_synthetic_cli.py`

**Interfaces:**
- Produces: `halyk-covenants generate-synthetic --output PATH` and `halyk-covenants benchmark --dataset PATH [--min-component-accuracy FLOAT]`.
- Consumes: generator, validator, benchmark runner, and report writers.

- [ ] **Step 1: Write failing Typer tests for generation, benchmark JSON summary output, and threshold-triggered nonzero exit**
- [ ] **Step 2: Implement both commands without mixing rendering or scoring logic into CLI callbacks**
- [ ] **Step 3: Verify existing `evaluate` CLI behavior remains unchanged and commit `feat: expose synthetic benchmark commands`**

### Task 6: Docker and Compose workflow

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker-compose.yml`
- Create: `scripts/docker-healthcheck.sh`
- Test: `tests/integration/test_docker_contract.py`

**Interfaces:**
- Produces: image entrypoint `halyk-covenants`, Compose services `generate-synthetic` and `benchmark`, bind-mounted `/app/data/synthetic`, and a CLI healthcheck.

- [ ] **Step 1: Write failing contract tests that parse Dockerfile/Compose and assert one image, non-root runtime, service commands, dependency ordering, volume path, and healthcheck**
- [ ] **Step 2: Implement a multi-stage Python 3.12 image that builds a wheel, installs DejaVu fonts, copies no development caches, and runs as `appuser`**
- [ ] **Step 3: Implement Compose sequencing with `condition: service_completed_successfully` and the shared bind mount**
- [ ] **Step 4: Run `docker compose config`, build the image, run the benchmark service, and compare its report hash to the host report**
- [ ] **Step 5: Commit `build: containerize synthetic benchmark workflow`**

### Task 7: Generated deliverables, documentation, and final QA

**Files:**
- Modify: `README.md`
- Create/update: `data/synthetic/**`

**Interfaces:**
- Produces: committed synthetic PDFs, XLSX, golden specs, Q&A, manifest, benchmark reports, and documented host/Docker commands.

- [ ] **Step 1: Generate the final dataset and benchmark reports through the installed host CLI**
- [ ] **Step 2: Render both PDFs to images and visually inspect Cyrillic text, tables, line wrapping, page breaks, and footnotes**
- [ ] **Step 3: Open the XLSX through OpenPyXL and DuckDB ingestion; verify sheet names, exact values, string IDs, duplicate preservation, and case calculations**
- [ ] **Step 4: Document artifact topology, intentional defects, benchmark scope, metrics, expected limitation, host commands, and Docker Compose command**
- [ ] **Step 5: Run `pytest -q`, `ruff check .`, `ruff format --check .`, `docker compose config`, Docker build/run, host benchmark twice, and `git diff --check`**
- [ ] **Step 6: Commit `docs: add reproducible synthetic benchmark artifacts`**
