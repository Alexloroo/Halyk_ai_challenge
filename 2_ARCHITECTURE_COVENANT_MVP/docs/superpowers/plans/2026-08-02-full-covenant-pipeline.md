# Full Covenant Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the traced raw-PDF-to-submission covenant MVP with local GPU OCR/VLM,
LangChain DeepSeek compilation, bounded LangGraph repair, deterministic DuckDB evaluation, and
strict verification.

**Architecture:** Preprocessing routes each PDF page through native extraction, PP-OCRv5, or
PaddleOCR-VL and compiles retrieved clauses through LangChain. Only ambiguous compilation and
repairable verification enter bounded LangGraph workflows. Python and DuckDB exclusively calculate
numbers and verdicts; every public stage is a nested LangSmith trace with a local audit record.

**Tech Stack:** Python 3.12, Pydantic 2, DuckDB, PyMuPDF, PaddleOCR 3, PaddlePaddle GPU 3.2,
LangChain 1.x, `langchain-deepseek`, LangGraph 1.x, LangSmith, BM25, FAISS,
sentence-transformers, RapidFuzz, Typer, pytest, Docker Compose, CUDA 12.6.

## Global Constraints

- Keep money as Python `Decimal` and DuckDB `DECIMAL(38, 6)`.
- Keep all identifiers as strings, including leading zeroes.
- Prefer native PDF text and invoke OCR only page-by-page when quality requires it.
- Use `ChatDeepSeek`; do not call the DeepSeek API directly from domain or pipeline modules.
- Never allow LangChain or LangGraph to write transaction values, number, comparator result, or
  verdict.
- Bound compiler repair at three attempts and verifier repair at two attempts.
- Decorate every public pipeline stage with `@traceable`; trace failures must not fail evaluation.
- Do not trace secrets, raw authorization data, hidden reasoning, full production PDFs, or full
  production transaction rows.
- Preserve `.env.example` and `Untitled-1.ipynb`, which contain user-owned uncommitted changes.
- Normal tests make no network calls and do not require GPU or DeepSeek credentials.
- Add behavior tests before production code and observe the expected failure for every cycle.

---

### Task 1: Tracing, configuration, and extended domain contracts

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/halyk_covenants/config.py`
- Modify: `src/halyk_covenants/domain/covenant.py`
- Modify: `src/halyk_covenants/domain/result.py`
- Create: `src/halyk_covenants/domain/document.py`
- Create: `src/halyk_covenants/domain/calculation.py`
- Create: `src/halyk_covenants/observability/tracing.py`
- Create: `src/halyk_covenants/observability/__init__.py`
- Test: `tests/unit/test_extended_domain.py`
- Test: `tests/unit/test_tracing.py`

**Interfaces:**
- Produces: `DocumentBlock`, `PageExtractionQuality`, `Calculation`, `PipelineStageRecord`.
- Produces: `trace_stage(name, run_type, redact_inputs, redact_outputs)` decorator.
- Produces: extended `CovenantSpec` fields `scope_mode`, `group_by`, `exclusions`, `date_field`,
  `covenant_group_id`, `status`, and `compiler_metadata`.

- [ ] **Step 1: Write failing domain and trace behavior tests**

```python
def test_group_covenant_requires_multiple_borrowers() -> None:
    with pytest.raises(ValidationError):
        CovenantSpec.model_validate({**BASE_SPEC, "scope_mode": "group", "borrower_ids": ["B1"]})


def test_trace_stage_redacts_declared_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)

    @trace_stage("transaction.ingest", redact_inputs={"rows"})
    def ingest(rows: list[dict[str, str]]) -> int:
        return len(rows)

    assert ingest([{"account": "secret"}]) == 1
```

- [ ] **Step 2: Run tests and verify missing models/decorator fail**

Run: `.venv/bin/pytest -q tests/unit/test_extended_domain.py tests/unit/test_tracing.py`
Expected: collection/import failure for the new contracts.

- [ ] **Step 3: Add LangChain/LangGraph/LangSmith/retrieval dependencies and minimal models**

Add core dependencies with compatible major-version ceilings and implement strict Pydantic
validators. `trace_stage` must wrap LangSmith `traceable`, process redacted arguments, preserve the
original signature, and behave normally when tracing is disabled.

- [ ] **Step 4: Run domain, tracing, and existing model tests**

Run: `.venv/bin/pytest -q tests/unit/test_extended_domain.py tests/unit/test_tracing.py tests/unit/test_domain_models.py`
Expected: PASS.

- [ ] **Step 5: Commit the contracts**

```bash
git add pyproject.toml src/halyk_covenants/config.py src/halyk_covenants/domain \
  src/halyk_covenants/observability tests/unit/test_extended_domain.py tests/unit/test_tracing.py
git commit -m "feat: add traced pipeline domain contracts"
```

### Task 2: Native PDF extraction and quality routing

**Files:**
- Create: `src/halyk_covenants/ingestion/pdf.py`
- Create: `src/halyk_covenants/ingestion/quality.py`
- Create: `src/halyk_covenants/documents/blocks.py`
- Create: `src/halyk_covenants/documents/__init__.py`
- Test: `tests/unit/test_page_quality.py`
- Test: `tests/integration/test_native_pdf_ingestion.py`

**Interfaces:**
- Produces: `PDFIngestor.ingest(path: Path) -> list[DocumentBlock]`.
- Produces: `PageQualityRouter.classify(page: NativePage) -> PageExtractionQuality`.
- Consumes later: optional `OCRProvider` and `VisualDocumentProvider` injected into `PDFIngestor`.

- [ ] **Step 1: Write failing native and scan-routing tests**

```python
def test_native_contract_uses_text_without_ocr(alpha_pdf: Path, failing_ocr: OCRProvider) -> None:
    blocks = PDFIngestor(ocr=failing_ocr).ingest(alpha_pdf)
    assert any("Финансовые ковенанты" in block.text for block in blocks)
    assert {block.extraction_method for block in blocks} == {"native"}


def test_empty_native_page_routes_to_ocr() -> None:
    quality = PageQualityRouter(native_text_min_chars=80).classify(
        NativePage(page=1, text="", image_count=1, table_count=0, width=595, height=842)
    )
    assert quality.route == "ocr"
```

- [ ] **Step 2: Verify the tests fail for missing ingestion classes**

Run: `.venv/bin/pytest -q tests/unit/test_page_quality.py tests/integration/test_native_pdf_ingestion.py`
Expected: import failure.

- [ ] **Step 3: Implement page models, routing, native blocks, and provenance**

Use PyMuPDF text dictionaries to preserve block bounding boxes. Generate stable IDs from document
hash, page, block index, and parser version. Decorate `classify`, native extraction, and `ingest`.

- [ ] **Step 4: Verify native ingestion and existing PDF fixture generation**

Run: `.venv/bin/pytest -q tests/unit/test_page_quality.py tests/integration/test_native_pdf_ingestion.py tests/integration/test_synthetic_renderers.py`
Expected: PASS.

- [ ] **Step 5: Commit native ingestion**

```bash
git add src/halyk_covenants/ingestion src/halyk_covenants/documents \
  tests/unit/test_page_quality.py tests/integration/test_native_pdf_ingestion.py
git commit -m "feat: add quality-routed native PDF ingestion"
```

### Task 3: GPU OCR, local visual parsing, and Docker profiles

**Files:**
- Create: `src/halyk_covenants/ocr/base.py`
- Create: `src/halyk_covenants/ocr/paddle.py`
- Create: `src/halyk_covenants/ocr/__init__.py`
- Create: `src/halyk_covenants/vlm/base.py`
- Create: `src/halyk_covenants/vlm/paddle_layout.py`
- Create: `src/halyk_covenants/vlm/__init__.py`
- Modify: `src/halyk_covenants/ingestion/pdf.py`
- Create: `Dockerfile.ocr`
- Modify: `docker-compose.yml`
- Create: `scripts/ocr-healthcheck.sh`
- Test: `tests/unit/test_ocr_fallback.py`
- Test: `tests/integration/test_scanned_pdf_ingestion.py`
- Test: `tests/integration/test_gpu_docker_contract.py`

**Interfaces:**
- Produces: `OCRProvider.extract(image: PageImage, device: str) -> OCRPageResult`.
- Produces: `VisualDocumentProvider.extract(image: PageImage) -> list[DocumentBlock]`.
- Produces: `PaddleOCRProvider` with GPU-first and CPU-retry behavior.

- [ ] **Step 1: Write failing GPU-to-CPU fallback and scanned PDF tests**

```python
def test_cuda_oom_retries_same_page_on_cpu() -> None:
    engine = RecordingPaddleEngine(gpu_error=MemoryError("CUDA out of memory"))
    result = PaddleOCRProvider(engine_factory=engine.factory).extract(PAGE_IMAGE, "gpu:0")
    assert result.text == "Лимит 5 000 000 KZT"
    assert engine.devices == ["gpu:0", "cpu"]
```

- [ ] **Step 2: Run tests and verify missing OCR/VLM adapters fail**

Run: `.venv/bin/pytest -q tests/unit/test_ocr_fallback.py tests/integration/test_scanned_pdf_ingestion.py`
Expected: import failure.

- [ ] **Step 3: Implement lazy Paddle adapters and PDF routing integration**

Keep Paddle imports inside production factories so the CPU test environment does not require the
GPU wheel. Normalize Paddle output into domain objects and classify CUDA/OOM errors for one CPU
retry. Route table/layout pages to the visual provider.

- [ ] **Step 4: Add runnable Docker behavior tests and GPU/CPU profiles**

The contract test must run `docker compose config --format json` and assert the OCR service has GPU
reservation, 8 GB shared memory, model cache, and the expected healthcheck. `Dockerfile.ocr` uses a
CUDA 12.6 runtime and installs PaddlePaddle GPU 3.2 and PaddleOCR 3.x.

- [ ] **Step 5: Run OCR and Docker contract tests**

Run: `.venv/bin/pytest -q tests/unit/test_ocr_fallback.py tests/integration/test_scanned_pdf_ingestion.py tests/integration/test_gpu_docker_contract.py`
Expected: PASS without requiring a local Paddle import.

- [ ] **Step 6: Commit OCR and GPU Docker support**

```bash
git add Dockerfile.ocr docker-compose.yml scripts/ocr-healthcheck.sh \
  src/halyk_covenants/ocr src/halyk_covenants/vlm src/halyk_covenants/ingestion/pdf.py \
  tests/unit/test_ocr_fallback.py tests/integration/test_scanned_pdf_ingestion.py \
  tests/integration/test_gpu_docker_contract.py
git commit -m "feat: add GPU OCR and local document VLM"
```

### Task 4: Lexical and semantic retrieval

**Files:**
- Create: `src/halyk_covenants/documents/retrieval.py`
- Create: `src/halyk_covenants/storage/artifact_store.py`
- Test: `tests/unit/test_retrieval.py`
- Test: `tests/integration/test_retrieval_cache.py`

**Interfaces:**
- Produces: `HybridRetriever.index(blocks: list[DocumentBlock]) -> IndexStats`.
- Produces: `HybridRetriever.search(query: str, document_id: str | None, k: int) -> list[RetrievedBlock]`.
- Consumes: injected `EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]`.

- [ ] **Step 1: Write failing retrieval ranking and cache tests**

```python
def test_definition_and_exception_rank_above_unrelated_blocks() -> None:
    retriever = HybridRetriever(embedder=LiteralEmbeddingProvider(VECTORS))
    retriever.index(BLOCKS)
    results = retriever.search("Permitted Payments tax exception", document_id="DOC1", k=2)
    assert [item.block.block_id for item in results] == ["definition", "exception"]
```

- [ ] **Step 2: Verify retrieval tests fail**

Run: `.venv/bin/pytest -q tests/unit/test_retrieval.py tests/integration/test_retrieval_cache.py`
Expected: import failure.

- [ ] **Step 3: Implement BM25, cosine/FAISS adapter, score fusion, and content-hash cache**

Use deterministic score normalization and stable block-ID tie-breaking. The production embedding
factory lazily loads a multilingual sentence-transformer; tests use literal embeddings.

- [ ] **Step 4: Run retrieval tests**

Run: `.venv/bin/pytest -q tests/unit/test_retrieval.py tests/integration/test_retrieval_cache.py`
Expected: PASS.

- [ ] **Step 5: Commit retrieval**

```bash
git add src/halyk_covenants/documents/retrieval.py \
  src/halyk_covenants/storage/artifact_store.py tests/unit/test_retrieval.py \
  tests/integration/test_retrieval_cache.py
git commit -m "feat: add hybrid covenant context retrieval"
```

### Task 5: Borrower normalization and resolution

**Files:**
- Create: `src/halyk_covenants/borrowers/normalization.py`
- Create: `src/halyk_covenants/borrowers/resolver.py`
- Create: `src/halyk_covenants/borrowers/__init__.py`
- Test: `tests/unit/test_borrower_resolver.py`

**Interfaces:**
- Produces: `BorrowerResolver.resolve(claim: BorrowerClaim) -> BorrowerResolution`.
- Produces statuses: `resolved_exact`, `resolved_alias`, `resolved_fuzzy`, `ambiguous`, `unresolved`.
- Consumes later: optional LangChain adjudicator only for ambiguous candidates.

- [ ] **Step 1: Write failing precedence and ambiguity tests**

```python
def test_exact_identifier_cannot_be_overridden_by_better_name_score() -> None:
    result = RESOLVER.resolve(BorrowerClaim(identifiers={"BIN": "9901"}, name="Beta"))
    assert result.borrower_ids == ["B001"]
    assert result.status == "resolved_exact"


def test_tied_fuzzy_candidates_remain_ambiguous() -> None:
    result = RESOLVER.resolve(BorrowerClaim(name="ALFA TRADE"))
    assert result.status == "ambiguous"
```

- [ ] **Step 2: Verify resolver tests fail**

Run: `.venv/bin/pytest -q tests/unit/test_borrower_resolver.py`
Expected: import failure.

- [ ] **Step 3: Implement normalization, exact precedence, aliases, and RapidFuzz thresholds**

Normalize legal-form punctuation and Unicode without stripping meaningful digits. Return ranked
candidates and evidence for every resolution.

- [ ] **Step 4: Run resolver tests**

Run: `.venv/bin/pytest -q tests/unit/test_borrower_resolver.py`
Expected: PASS.

- [ ] **Step 5: Commit borrower resolution**

```bash
git add src/halyk_covenants/borrowers tests/unit/test_borrower_resolver.py
git commit -m "feat: add precedence-safe borrower resolution"
```

### Task 6: Covenant discovery and LangChain DeepSeek compiler

**Files:**
- Create: `src/halyk_covenants/llm/client.py`
- Create: `src/halyk_covenants/llm/prompts/compiler.py`
- Create: `src/halyk_covenants/llm/__init__.py`
- Create: `src/halyk_covenants/covenants/detector.py`
- Create: `src/halyk_covenants/covenants/compiler.py`
- Create: `src/halyk_covenants/covenants/validation.py`
- Create: `src/halyk_covenants/covenants/__init__.py`
- Test: `tests/unit/test_covenant_detector.py`
- Test: `tests/unit/test_covenant_compiler.py`
- Test: `tests/integration/test_deepseek_factory.py`

**Interfaces:**
- Produces: `DeepSeekChatFactory.create() -> BaseChatModel`.
- Produces: `CovenantDetector.detect(blocks) -> list[CovenantCandidate]`.
- Produces: `CovenantCompiler.compile(candidate, context) -> CompilationOutcome`.

- [ ] **Step 1: Write failing multi-rule and structured compiler tests**

```python
def test_detector_splits_two_independently_scored_conditions() -> None:
    candidates = CovenantDetector().detect([PARAGRAPH_WITH_SUM_AND_COUNT])
    assert [candidate.ordinal for candidate in candidates] == [1, 2]


def test_compiler_rejects_llm_threshold_without_currency_context() -> None:
    compiler = CovenantCompiler(model=FakeStructuredModel(DRAFT_WITH_MISSING_CURRENCY))
    outcome = compiler.compile(CANDIDATE, CONTEXT)
    assert outcome.route == "ambiguous"
    assert "currency" in outcome.validation_errors
```

- [ ] **Step 2: Verify detector/compiler tests fail**

Run: `.venv/bin/pytest -q tests/unit/test_covenant_detector.py tests/unit/test_covenant_compiler.py tests/integration/test_deepseek_factory.py`
Expected: import failure.

- [ ] **Step 3: Implement high-recall detection, ChatDeepSeek factory, prompt, and validation**

Use `with_structured_output` with a wrapper model containing `specs: list[CovenantSpec]`. Factory
must fail with a credential-specific configuration error when `DEEPSEEK_API_KEY` is absent. It
must not inspect or print the key.

- [ ] **Step 4: Run compiler tests without network**

Run: `.venv/bin/pytest -q tests/unit/test_covenant_detector.py tests/unit/test_covenant_compiler.py tests/integration/test_deepseek_factory.py`
Expected: PASS with fake models.

- [ ] **Step 5: Commit LangChain compiler**

```bash
git add src/halyk_covenants/llm src/halyk_covenants/covenants \
  tests/unit/test_covenant_detector.py tests/unit/test_covenant_compiler.py \
  tests/integration/test_deepseek_factory.py
git commit -m "feat: compile covenant rules through LangChain DeepSeek"
```

### Task 7: Bounded compiler LangGraph

**Files:**
- Create: `src/halyk_covenants/covenants/compiler_graph.py`
- Test: `tests/unit/test_compiler_graph.py`

**Interfaces:**
- Produces: `CompilerGraph.invoke(initial: CompilerState) -> CompilerState`.
- Consumes: `CovenantCompiler`, `HybridRetriever`, and deterministic semantic validator.

- [ ] **Step 1: Write failing direct-route, repair, and exhaustion tests**

```python
def test_straightforward_spec_never_calls_repair_model() -> None:
    graph = CompilerGraph(compiler=VALID_COMPILER, repair_model=FailIfCalledModel())
    final = graph.invoke(VALID_INITIAL_STATE)
    assert final["status"] == "compiled"
    assert final["attempt"] == 0


def test_invalid_repairs_stop_after_three_attempts() -> None:
    graph = CompilerGraph(compiler=INVALID_COMPILER, repair_model=AlwaysInvalidModel())
    final = graph.invoke(AMBIGUOUS_INITIAL_STATE)
    assert final["status"] == "failed_compilation"
    assert final["attempt"] == 3
```

- [ ] **Step 2: Verify graph tests fail**

Run: `.venv/bin/pytest -q tests/unit/test_compiler_graph.py`
Expected: import failure.

- [ ] **Step 3: Implement typed StateGraph, conditional edges, and proactive attempt limit**

Do not use a general agent or tool node. Each node returns explicit state updates. Decorate graph
entry and nodes; validate every draft after every model call.

- [ ] **Step 4: Run graph tests**

Run: `.venv/bin/pytest -q tests/unit/test_compiler_graph.py`
Expected: PASS.

- [ ] **Step 5: Commit compiler graph**

```bash
git add src/halyk_covenants/covenants/compiler_graph.py tests/unit/test_compiler_graph.py
git commit -m "feat: add bounded ambiguous compiler graph"
```

### Task 8: Persistent registry, temporal resolver, and audit records

**Files:**
- Modify: `src/halyk_covenants/storage/duckdb_store.py`
- Create: `src/halyk_covenants/covenants/registry.py`
- Create: `src/halyk_covenants/covenants/temporal.py`
- Test: `tests/integration/test_covenant_registry.py`
- Test: `tests/unit/test_temporal_resolver.py`

**Interfaces:**
- Produces: `CovenantRegistry.save/list/for_borrower`.
- Produces: `TemporalResolver.resolve(group_id, borrower_id, at_date) -> CovenantSpec`.
- Produces: DuckDB persistence for documents, blocks, aliases, versions, calculations, results,
  and stage records.

- [ ] **Step 1: Write failing persistence and temporal conflict tests**

```python
def test_april_and_june_resolve_different_versions() -> None:
    assert RESOLVER.resolve("LIMIT", "B001", date(2026, 4, 30)).condition.threshold == 10_000_000
    assert RESOLVER.resolve("LIMIT", "B001", date(2026, 6, 30)).condition.threshold == 15_000_000


def test_overlapping_versions_fail_explicitly() -> None:
    with pytest.raises(OverlappingCovenantVersions):
        OVERLAPPING.resolve("LIMIT", "B001", date(2026, 5, 1))
```

- [ ] **Step 2: Verify registry/temporal tests fail**

Run: `.venv/bin/pytest -q tests/integration/test_covenant_registry.py tests/unit/test_temporal_resolver.py`
Expected: import or schema failure.

- [ ] **Step 3: Add idempotent schema migration, JSON persistence, and exact interval selection**

Persist strict model JSON and normalized lookup columns. Preserve compatibility with existing
transaction tables and in-memory stores.

- [ ] **Step 4: Run storage and temporal tests**

Run: `.venv/bin/pytest -q tests/integration/test_covenant_registry.py tests/unit/test_temporal_resolver.py tests/integration/test_duckdb_ingestion.py`
Expected: PASS.

- [ ] **Step 5: Commit registry and temporal resolution**

```bash
git add src/halyk_covenants/storage/duckdb_store.py src/halyk_covenants/covenants \
  tests/integration/test_covenant_registry.py tests/unit/test_temporal_resolver.py
git commit -m "feat: persist covenant versions and audit artifacts"
```

### Task 9: Ratio, existence, frequency, group scope, exclusions, and currency guard

**Files:**
- Modify: `src/halyk_covenants/sql/builder.py`
- Modify: `src/halyk_covenants/sql/filters.py`
- Modify: `src/halyk_covenants/evaluators/base.py`
- Create: `src/halyk_covenants/evaluators/ratio.py`
- Create: `src/halyk_covenants/evaluators/existence.py`
- Create: `src/halyk_covenants/evaluators/frequency.py`
- Modify: `src/halyk_covenants/evaluators/registry.py`
- Test: `tests/integration/test_advanced_evaluators.py`
- Test: `tests/unit/test_currency_guard.py`

**Interfaces:**
- Extends: `build_where_clause` accepts borrower IDs, exclusions, date field, and scope mode.
- Produces: registered `ratio`, `existence`, and `frequency` evaluators.

- [ ] **Step 1: Write failing literal-result tests for every advanced metric**

```python
@pytest.mark.parametrize(
    ("case", "number", "verdict"),
    [
        ("ratio", Decimal("0.4"), "violated"),
        ("existence", 1, "violated"),
        ("frequency", 4, "violated"),
    ],
)
def test_advanced_metric_literals(case: str, number: Decimal | int, verdict: str) -> None:
    result = evaluate_fixture(case)
    assert result.number == number
    assert result.verdict == verdict


def test_mixed_currency_sum_without_currency_filter_fails_closed() -> None:
    result = SERVICE.evaluate(MONEY_SPEC_WITHOUT_CURRENCY_FILTER, "B001", date(2026, 4, 30))
    assert result.status == "failed"
    assert result.verdict == "unknown"
```

- [ ] **Step 2: Verify advanced evaluator tests fail**

Run: `.venv/bin/pytest -q tests/integration/test_advanced_evaluators.py tests/unit/test_currency_guard.py`
Expected: unsupported metric and missing guard failures.

- [ ] **Step 3: Implement parameterized group/exclusion SQL and evaluators**

Ratio denominator zero returns undefined. Grouped ratio returns the maximum group value. Frequency
uses deterministic bucket expressions for day/week/month. Currency guard queries matching distinct
currencies before money aggregation and allows one currency or an explicit FX policy only.

- [ ] **Step 4: Run all evaluator tests**

Run: `.venv/bin/pytest -q tests/integration/test_advanced_evaluators.py tests/unit/test_currency_guard.py tests/integration/test_evaluators.py`
Expected: PASS.

- [ ] **Step 5: Commit evaluator extensions**

```bash
git add src/halyk_covenants/sql src/halyk_covenants/evaluators \
  tests/integration/test_advanced_evaluators.py tests/unit/test_currency_guard.py
git commit -m "feat: add advanced deterministic covenant metrics"
```

### Task 10: Evidence selector registry

**Files:**
- Create: `src/halyk_covenants/evidence/selectors.py`
- Create: `src/halyk_covenants/evidence/validation.py`
- Create: `src/halyk_covenants/evidence/__init__.py`
- Modify: `src/halyk_covenants/evaluators/base.py`
- Test: `tests/integration/test_evidence_selectors.py`

**Interfaces:**
- Produces: `EvidenceSelectorRegistry.select(context: EvidenceContext) -> str | None`.
- Produces: `EvidenceValidator.validate(transaction_id, context) -> EvidenceVerification`.

- [ ] **Step 1: Write failing trigger, prohibition, frequency, and tie-break tests**

```python
def test_count_trigger_is_limit_plus_one_in_chronological_order() -> None:
    result = SERVICE.evaluate(COUNT_LIMIT_TWO, "B001", date(2026, 4, 30))
    assert result.evidence_transaction_id == "A003"
    assert result.status == "success"
```

- [ ] **Step 2: Verify evidence tests fail on the current partial result**

Run: `.venv/bin/pytest -q tests/integration/test_evidence_selectors.py`
Expected: `A003` expected but `None` returned.

- [ ] **Step 3: Implement independent selectors and post-selection validation**

Use filtered SQL shared with metric execution and stable ordering. Do not discard a valid number or
verdict when selector/validation fails.

- [ ] **Step 4: Run evidence and synthetic benchmark tests**

Run: `.venv/bin/pytest -q tests/integration/test_evidence_selectors.py tests/integration/test_benchmark_runner.py`
Expected: selector tests pass; synthetic benchmark improves from 29/30 to 30/30 after updating the
expected baseline test.

- [ ] **Step 5: Commit evidence selectors**

```bash
git add src/halyk_covenants/evidence src/halyk_covenants/evaluators/base.py \
  tests/integration/test_evidence_selectors.py tests/integration/test_benchmark_runner.py
git commit -m "feat: add deterministic evidence selectors"
```

### Task 11: Deterministic verifier and bounded repair graph

**Files:**
- Create: `src/halyk_covenants/verification/models.py`
- Create: `src/halyk_covenants/verification/verifier.py`
- Create: `src/halyk_covenants/verification/repair_graph.py`
- Create: `src/halyk_covenants/verification/__init__.py`
- Test: `tests/unit/test_verifier.py`
- Test: `tests/unit/test_repair_graph.py`

**Interfaces:**
- Produces: `ResultVerifier.verify(expected_pairs, results, context) -> VerificationReport`.
- Produces: `RepairGraph.invoke(RepairState) -> RepairState`.
- Enforces: repair patches are limited to whitelisted CovenantSpec/mapping/evidence paths.

- [ ] **Step 1: Write failing corruption and repair-authorization tests**

```python
def test_verifier_detects_number_verdict_mismatch() -> None:
    report = VERIFIER.verify_pair(SPEC, RESULT_WITH_CORRUPTED_VERDICT, CONTEXT)
    assert report.issues[0].code == "verdict_mismatch"
    assert report.issues[0].classification == "non_repairable"


def test_repair_patch_cannot_write_number_or_transactions() -> None:
    graph = RepairGraph(proposer=FakeProposer({"number": 1, "transactions": []}))
    final = graph.invoke(REPAIR_STATE)
    assert final["status"] == "rejected_unauthorized_patch"
```

- [ ] **Step 2: Verify verifier/repair tests fail**

Run: `.venv/bin/pytest -q tests/unit/test_verifier.py tests/unit/test_repair_graph.py`
Expected: import failure.

- [ ] **Step 3: Implement reproduction checks, issue classification, patch whitelist, and graph**

Every accepted repair revalidates the spec, reruns `EvaluationService`, and reruns the verifier.
Stop after two proposals. Transaction snapshot hash must remain identical throughout the graph.

- [ ] **Step 4: Run verifier and repair tests**

Run: `.venv/bin/pytest -q tests/unit/test_verifier.py tests/unit/test_repair_graph.py`
Expected: PASS.

- [ ] **Step 5: Commit verifier and repair graph**

```bash
git add src/halyk_covenants/verification tests/unit/test_verifier.py tests/unit/test_repair_graph.py
git commit -m "feat: verify and safely repair covenant evaluations"
```

### Task 12: Preprocess and batch evaluation pipelines with full tracing

**Files:**
- Create: `src/halyk_covenants/pipeline/preprocess.py`
- Create: `src/halyk_covenants/pipeline/evaluate.py`
- Create: `src/halyk_covenants/pipeline/__init__.py`
- Modify: `src/halyk_covenants/cli.py`
- Test: `tests/integration/test_preprocess_pipeline.py`
- Test: `tests/integration/test_batch_pipeline.py`
- Test: `tests/integration/test_trace_topology.py`

**Interfaces:**
- Produces: `PreprocessPipeline.run(input_root, at_date) -> PreprocessReport`.
- Produces: `BatchEvaluationPipeline.run(at_date) -> BatchEvaluationReport`.
- Produces CLI commands `preprocess`, `inspect-covenants`, `evaluate-all`, `verify-results`,
  `benchmark-full`, and `ocr-smoke`.

- [ ] **Step 1: Write failing idempotency, completeness, and trace-tree tests**

```python
def test_batch_keeps_failed_pair_and_preserves_completeness() -> None:
    report = PIPELINE.run(date(2026, 4, 30))
    assert report.expected_pair_count == report.actual_pair_count == 3
    assert {result.status for result in report.results} == {"success", "failed"}


def test_root_pipeline_contains_deterministic_child_stages(
    recording_tracer: RecordingTracer,
) -> None:
    PIPELINE.run(date(2026, 4, 30))
    assert recording_tracer.paths() >= {
        "pipeline.evaluate/sql.build",
        "pipeline.evaluate/evaluator.calculate",
        "pipeline.evaluate/comparator.compare",
    }
```

- [ ] **Step 2: Verify pipeline tests fail**

Run: `.venv/bin/pytest -q tests/integration/test_preprocess_pipeline.py tests/integration/test_batch_pipeline.py tests/integration/test_trace_topology.py`
Expected: import failure.

- [ ] **Step 3: Implement hash-idempotent preprocessing, completeness matrix, audit writes, and CLI**

Root methods and child stages must be traceable. Store local stage records regardless of remote
tracing state. Preserve per-pair fault isolation.

- [ ] **Step 4: Run pipeline and existing CLI tests**

Run: `.venv/bin/pytest -q tests/integration/test_preprocess_pipeline.py tests/integration/test_batch_pipeline.py tests/integration/test_trace_topology.py tests/integration/test_cli.py`
Expected: PASS.

- [ ] **Step 5: Commit pipelines and CLI**

```bash
git add src/halyk_covenants/pipeline src/halyk_covenants/cli.py \
  tests/integration/test_preprocess_pipeline.py tests/integration/test_batch_pipeline.py \
  tests/integration/test_trace_topology.py
git commit -m "feat: add traced preprocess and batch evaluation pipelines"
```

### Task 13: Strict configurable submission serializer

**Files:**
- Create: `src/halyk_covenants/submission/models.py`
- Create: `src/halyk_covenants/submission/serializer.py`
- Create: `src/halyk_covenants/submission/validator.py`
- Create: `src/halyk_covenants/submission/__init__.py`
- Create: `configs/submission/synthetic.yaml`
- Modify: `src/halyk_covenants/cli.py`
- Test: `tests/unit/test_submission_serializer.py`
- Test: `tests/fixtures/submission/synthetic_golden.json`
- Test: `tests/integration/test_submission_cli.py`

**Interfaces:**
- Produces: `SubmissionSerializer(profile).serialize(results) -> dict[str, object]`.
- Produces: `SubmissionValidator(profile).validate(payload) -> SubmissionValidationReport`.
- Produces CLI commands `serialize-submission` and `validate-submission`.

- [ ] **Step 1: Write failing ratio-format, null, ordering, and extra-key tests**

```python
def test_synthetic_profile_serializes_ratio_as_percentage() -> None:
    payload = SERIALIZER.serialize([RATIO_RESULT_034])
    assert payload["answers"][0]["number"] == "34"


def test_strict_validator_rejects_extra_keys() -> None:
    report = VALIDATOR.validate({"answers": [], "unexpected": True})
    assert report.valid is False
```

- [ ] **Step 2: Verify serializer tests fail**

Run: `.venv/bin/pytest -q tests/unit/test_submission_serializer.py tests/integration/test_submission_cli.py`
Expected: import/CLI failure.

- [ ] **Step 3: Implement strict profile loading, mapping, validation, and golden serialization**

Keep all presentation rules outside evaluators. Serialize Decimal as strings and sort by borrower
then covenant unless the profile declares another order.

- [ ] **Step 4: Run serializer tests**

Run: `.venv/bin/pytest -q tests/unit/test_submission_serializer.py tests/integration/test_submission_cli.py`
Expected: PASS and exact golden-file match.

- [ ] **Step 5: Commit serializer**

```bash
git add configs/submission src/halyk_covenants/submission src/halyk_covenants/cli.py \
  tests/unit/test_submission_serializer.py tests/integration/test_submission_cli.py \
  tests/fixtures/submission/synthetic_golden.json
git commit -m "feat: add strict configurable submission serialization"
```

### Task 14: Full synthetic end-to-end, live smoke gates, and documentation

**Files:**
- Modify: `src/halyk_covenants/synthetic/pdf.py`
- Modify: `src/halyk_covenants/synthetic/definitions.py`
- Create: `tests/fixtures/scanned/README.md`
- Create: `tests/integration/test_full_pipeline_e2e.py`
- Create: `tests/live/test_deepseek_smoke.py`
- Create: `tests/live/test_gpu_ocr_smoke.py`
- Create: `tests/live/test_langsmith_smoke.py`
- Modify: `README.md`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: a deterministic end-to-end fixture that starts with PDF/XLSX and ends with a validated
  submission candidate.
- Produces: opt-in live tests selected by `RUN_DEEPSEEK_LIVE`, `RUN_GPU_OCR_LIVE`, and
  `RUN_LANGSMITH_LIVE`.

- [ ] **Step 1: Write failing full-pipeline exact-result test**

```python
def test_full_synthetic_pipeline_reaches_valid_submission(tmp_path: Path) -> None:
    report = run_full_synthetic_pipeline(tmp_path, model=FIXTURE_COMPILER_MODEL)
    assert report.compilation.failed == 0
    assert report.evaluation.actual_pair_count == report.evaluation.expected_pair_count
    assert report.verification.valid is True
    assert report.submission == GOLDEN_SUBMISSION
```

- [ ] **Step 2: Verify the end-to-end test fails before orchestration is connected**

Run: `.venv/bin/pytest -q tests/integration/test_full_pipeline_e2e.py`
Expected: missing full-pipeline entry point or incomplete result.

- [ ] **Step 3: Connect the final orchestration and document exact local/Docker commands**

Document credential names without values, tracing payload modes, CPU/GPU profiles, model cache,
DeepSeek/LangSmith opt-in tests, and the deterministic repair authority boundary.

- [ ] **Step 4: Run the complete offline verification suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass; live tests are skipped unless their explicit flags are set.

Run: `.venv/bin/ruff check . && .venv/bin/ruff format --check . && git diff --check`
Expected: all checks pass.

- [ ] **Step 5: Build and verify Docker runtimes**

Run: `docker compose build`
Expected: lightweight runtime builds.

Run: `docker compose --profile gpu config --quiet`
Expected: valid GPU profile.

Run: `docker compose run --rm benchmark`
Expected: deterministic evaluator benchmark passes its acceptance threshold.

The GPU OCR and live DeepSeek/LangSmith smoke commands run only when the corresponding runtime and
credentials are available. Their absence must be reported as skipped, not as an offline failure.

- [ ] **Step 6: Commit end-to-end delivery**

```bash
git add README.md docker-compose.yml src/halyk_covenants/synthetic \
  tests/fixtures/scanned tests/integration/test_full_pipeline_e2e.py tests/live
git commit -m "test: verify full traced covenant pipeline"
```

## Plan self-review coverage

- Tasks 1–3 cover domain contracts, tracing, native PDF, GPU OCR/VLM, and Docker.
- Tasks 4–7 cover retrieval, borrower resolution, LangChain DeepSeek, and compiler LangGraph.
- Tasks 8–10 cover registry, temporal resolution, advanced deterministic metrics, and evidence.
- Tasks 11–13 cover verifier repair, batch orchestration, trace topology, and submission output.
- Task 14 covers the raw-document-to-submission path, offline regression, Docker, and opt-in live
  validation.
- Every LLM/graph output crosses deterministic Pydantic or verifier validation.
- No task grants a model authority over raw transactions, number, comparator, or verdict.
