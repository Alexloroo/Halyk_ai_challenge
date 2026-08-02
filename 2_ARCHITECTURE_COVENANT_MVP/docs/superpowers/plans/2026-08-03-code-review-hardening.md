# Code Review Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the code-review findings that can affect private-dataset correctness, excluding stale PDF covenant cleanup because the competition run starts from a clean database.

**Architecture:** Preserve the core boundary `LLM interprets -> Pydantic validates -> DuckDB/Python calculates`. Harden the layers around it: effective-time intersection, idempotent/atomic structured ingestion, a single executable field catalog, deterministic covenant identity, structured document/table candidates, retrieval-backed compiler context, cross-document covenant-family linking, stronger verification, and richer evaluation coverage.

**Tech Stack:** Python 3.12, Pydantic v2, DuckDB, PyMuPDF, PaddleOCR/PPStructureV3, LangChain/LangGraph, rank-bm25, pytest, Ruff.

## Global Constraints

- Work only on branch `codex-1`.
- Do not implement stale-covenant cleanup for changed PDFs; the private run starts from a clean database.
- LLM must never calculate transaction metrics or own authoritative source identifiers.
- All SQL identifiers must come from closed deterministic catalogs; values stay parameterized.
- A failed source-file ingestion must leave no partial transaction snapshot.
- Existing synthetic and regression behavior must remain reproducible.

---

### Task 1: Deterministic correctness and storage

**Files:**
- Modify: `src/halyk_covenants/evaluators/temporal.py`
- Modify: `src/halyk_covenants/storage/duckdb_store.py`
- Create: `src/halyk_covenants/sql/fields.py`
- Modify: `src/halyk_covenants/sql/filters.py`
- Modify: `src/halyk_covenants/domain/covenant.py`
- Modify: `src/halyk_covenants/covenants/validation.py`
- Modify: `src/halyk_covenants/evaluators/ratio.py`
- Modify: `src/halyk_covenants/pipeline/evaluate.py`
- Modify: `src/halyk_covenants/evaluators/service.py`
- Test: unit/integration tests for effective windows, idempotent replacement, derived fields, pair verification, unsupported rules, and ratio sub-filters.

**Interfaces:**
- Produces: deterministic effective-window clipping for every covenant version.
- Produces: source-level transaction replacement in one DuckDB transaction.
- Produces: one `FieldCatalog` used by domain validation and SQL compilation.
- Produces: batch verification that checks both completeness and each result/verdict relationship.

- [ ] Write failing tests for a single version beginning mid-window and ending mid-window.
- [ ] Write failing test proving a changed transaction file replaces, rather than appends, its prior source snapshot.
- [ ] Write failing test that `weekday` survives compiler validation because it is a registered derived field.
- [ ] Write failing tests for unsupported specs and pair-verification integration.
- [ ] Implement minimal production changes and run focused tests.
- [ ] Run full pytest and Ruff.

### Task 2: Document intelligence, identity, and retrieval

**Files:**
- Modify: `src/halyk_covenants/vlm/paddle_layout.py`
- Modify: `src/halyk_covenants/ingestion/pdf.py`
- Modify: `src/halyk_covenants/pipeline/preprocess.py`
- Modify: `src/halyk_covenants/covenants/detector.py`
- Modify: `src/halyk_covenants/covenants/compiler.py`
- Create: `src/halyk_covenants/covenants/identity.py`
- Create: `src/halyk_covenants/documents/candidates.py`
- Modify: `src/halyk_covenants/documents/retrieval.py`
- Modify: `src/halyk_covenants/cli.py`
- Test: table-row assembly, layout caching, multi-borrower scope, deterministic IDs, family linking, and retrieval context.

**Interfaces:**
- Produces: table cells with stable `table_id`, `row_index`, and `column_index` when PPStructure provides table structure.
- Produces: document candidates assembled across adjacent text blocks/table rows rather than one block only.
- Produces: deterministic `covenant_id`/family identity outside the LLM.
- Produces: compiler context selected by metadata + hybrid retrieval instead of the entire PDF per candidate.

- [ ] Write failing tests for table-row reconstruction and cached layout model construction.
- [ ] Write failing tests for adjacent-block covenant detection and scope reset at structural boundaries.
- [ ] Write failing tests that per-spec borrower subsets are preserved.
- [ ] Write failing tests for deterministic IDs and cross-document family matching by explicit covenant/reference code.
- [ ] Write failing test that compiler context is top-k retrieval rather than whole-document repetition.
- [ ] Implement minimal changes and run focused tests.
- [ ] Run full pytest and Ruff.

### Task 3: Provenance, verification, and evaluation coverage

**Files:**
- Modify: `src/halyk_covenants/domain/calculation.py`
- Modify: evaluator base/aggregate/frequency/ratio modules as required.
- Modify: `src/halyk_covenants/evidence/validators.py`
- Modify: `src/halyk_covenants/evals/scoring.py`
- Modify: `src/halyk_covenants/config.py`
- Modify: `src/halyk_covenants/cli.py`
- Test: calculation ledger persistence, evidence semantic validation, compiler-field scoring, and config wiring.

**Interfaces:**
- Produces: persisted calculation records carrying SQL/parameters/input row count/value and linked `calculation_id` on results.
- Produces: semantic evidence validation for max/violating/trigger modes.
- Produces: compiler evals covering exclusions, group-by, effective dates, units, nested ratio metrics, and status.

- [ ] Write failing tests for calculation persistence and semantic evidence validation.
- [ ] Extend compiler scoring tests for previously unscored DSL fields.
- [ ] Wire configuration values that previously existed but were ignored (`native_text_min_chars`, tracing flags where applicable).
- [ ] Implement minimal changes and run focused tests.
- [ ] Run `ruff check src tests` and `pytest -q`.
- [ ] Confirm GitHub Actions for the final `codex-1` head is green.
