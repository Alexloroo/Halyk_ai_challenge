# Full Covenant Pipeline Design

**Date:** 2026-08-02  
**Status:** Approved for implementation  
**Base:** Phases 1–3 deterministic evaluator and synthetic benchmark

## 1. Objective

Complete the covenant MVP from raw documents and structured transactions through compilation,
deterministic evaluation, repair-aware verification, and strict serialization. All stages must be
observable as nested LangSmith traces. Language reasoning uses DeepSeek through LangChain. Visual
document parsing runs locally with PaddleOCR on the user's NVIDIA RTX 5060 8 GB GPU.

The system remains deterministic at evaluation time. LangChain and LangGraph may compile or repair
machine-readable rules, but they may never directly assign transaction values, calculated numbers,
or final verdicts.

## 2. Non-negotiable constraints

- Python 3.12 remains the application runtime.
- DuckDB `DECIMAL(38, 6)` and Python `Decimal` remain the monetary types.
- Identifiers remain strings, including values with leading zeroes.
- Native PDF text is preferred; OCR is page-selective, not unconditional.
- PP-OCRv5 Cyrillic is the default OCR model.
- PaddleOCR-VL/PP-Structure is used only for complex layout and table pages.
- OCR processes one page at a time by default to fit in 8 GB VRAM.
- DeepSeek is accessed only through `langchain-deepseek` and `ChatDeepSeek`.
- The default language model is `deepseek-v4-pro` and is configurable.
- LangGraph is used only for bounded ambiguous-compilation and verification-repair loops.
- Every public pipeline stage is decorated with LangSmith `@traceable`.
- Missing API credentials disable LLM-dependent stages explicitly; they never trigger fabricated
  output.
- LangSmith unavailability cannot change or stop deterministic evaluation.
- No API keys, hidden reasoning, raw authorization headers, or environment contents are traced.
- The user's existing uncommitted `.env.example` and `Untitled-1.ipynb` are not modified.
- The official submission schema is isolated behind a profile because it is not yet available.

## 3. End-to-end architecture

```text
PREPROCESSING
    raw PDF
       |
       v
    page quality routing
       |
       +--> PyMuPDF native text
       +--> PP-OCRv5 Cyrillic GPU
       +--> PaddleOCR-VL / PP-Structure GPU
       |
       v
    DocumentBlock registry
       |
       v
    lexical + vector retrieval
       |
       v
    LangChain ChatDeepSeek
       |
       v
    Covenant Compiler
       |
       +--> straightforward --> validated CovenantSpec --> DONE
       |
       +--> ambiguous --> bounded LangGraph compiler loop --> DONE / failed_compilation

EVALUATION
    CovenantSpec registry + DuckDB transactions
       |
       v
    Python deterministic evaluators
       |
       v
    number + verdict + evidence

VERIFICATION
    deterministic verifier
       |
       +--> OK --> END
       |
       +--> repairable --> bounded LangGraph repair loop
                              |
                              v
                       deterministic reevaluation
                              |
                              v
                       deterministic reverification
```

## 4. Domain model extensions

### 4.1 Document artifacts

`PageExtractionQuality` stores native text size, density, image/table counts, selected extraction
method, OCR/VLM requirement, and confidence.

`DocumentBlock` stores:

```text
block_id, document_id, page, block_type, text, bbox, table_id,
row_index, column_index, borrower_ids, extraction_method,
confidence, source
```

Block types are `text`, `table`, `table_cell`, `image`, `header`, and `footer`.

### 4.2 Covenant model

`CovenantSpec` gains:

```text
covenant_group_id
scope_mode: per_borrower | group
group_by: list[str]
exclusions: list[FilterSpec]
date_field
status: compiled | unsupported | failed_compilation
compiler_metadata
```

`MetricSpec` continues to support nested numerator and denominator metrics. Ratio evaluation may
group a numerator by counterparty and select the maximum ratio. Frequency evaluation groups by the
requested calendar grain and returns the maximum observed bucket count.

### 4.3 Provenance and observability

`Calculation` stores SQL, parameters in redacted form, input row count, metric value, unit,
calculation ID, trace ID, evaluator version, and timestamps.

`PipelineStageRecord` stores run ID, trace ID, parent trace ID, stage name, artifact path, status,
latency, and error summary. It is written locally even when LangSmith is unavailable.

## 5. PDF ingestion and GPU OCR

### 5.1 Page router

PyMuPDF extracts native text and page metadata first. Configurable quality thresholds route a page:

- `native`: sufficient readable native text;
- `ocr`: scanned page or insufficient text;
- `layout`: tables, multi-column layout, or low-confidence OCR;
- `failed`: neither GPU nor CPU extraction produced usable content.

### 5.2 OCR adapter

The application depends on an `OCRProvider` protocol. The production implementation wraps
PP-OCRv5 Cyrillic. It returns lines, bounding boxes, and confidence without leaking Paddle-specific
objects into domain modules.

GPU policy:

- device `gpu:0` when Paddle reports CUDA availability;
- one page per invocation;
- bounded input resolution;
- one retry after clearing page-scoped resources;
- CPU retry on CUDA OOM or unsupported kernel;
- explicit `ocr_failed` after both paths fail.

### 5.3 Local visual parser

`VisualDocumentProvider` wraps PaddleOCR-VL/PP-Structure for tables and complex layouts. The public
DeepSeek API has no image input, so DeepSeek receives only normalized text, table cells, bounding
box summaries, and retrieved context.

### 5.4 Docker

The existing lightweight image remains for deterministic evaluation. `Dockerfile.ocr` uses a CUDA
12.6/cuDNN runtime compatible with the installed NVIDIA driver and installs PaddlePaddle GPU plus
PaddleOCR. Compose exposes a `gpu` profile with `gpus: all`, `shm_size: 8gb`, and persistent model
cache. A CPU profile exercises fallback without an NVIDIA runtime.

## 6. Retrieval

Every `DocumentBlock` is indexed in:

- BM25 for exact clauses, IDs, definitions, and amendments;
- a local multilingual embedding index for semantic recall;
- an artifact registry keyed by document and content hash.

Retrieval returns blocks with source references and scores. Compiler context includes only the
candidate clause, borrower context, referenced definitions, nearby exceptions, and amendments.
Unchanged blocks are not re-embedded.

## 7. Borrower resolution

Resolution precedence is fixed:

```text
exact borrower/customer ID
exact BIN/IIN
exact account/IBAN
exact contract ID
normalized name
explicit alias
fuzzy name
DeepSeek adjudication
```

Exact identifiers cannot be overridden by fuzzy or LLM matches. Ambiguous candidates remain
explicit and route the covenant to the compiler graph. Group scope preserves all borrower IDs and
evaluates them in one SQL predicate.

## 8. LangChain Covenant Compiler

`DeepSeekChatFactory` creates `ChatDeepSeek` from `DEEPSEEK_API_KEY`, configurable base URL,
model, timeout, and bounded retries. The compiler uses a LangChain prompt and structured Pydantic
output. Prompts require JSON, independent rule splitting, exact comparators, units, currency,
periods, exceptions, evidence mode, and source provenance.

No hidden reasoning is stored. Only the final structured response, token usage, model, prompt
version, latency, and validation outcome are retained.

### 8.1 Straightforward route

A draft completes without LangGraph when deterministic semantic validation confirms borrower
scope, supported metric, field, comparator, threshold, unit/currency consistency, time window,
source, and minimum confidence.

### 8.2 Compiler LangGraph

`CompilerState` contains raw clause, context blocks, borrower candidates, draft specs, validation
errors, confidence, and attempt count.

Nodes:

```text
draft -> validate -> route
route ambiguous -> enrich_context -> repair_draft -> validate
route valid -> END
route exhausted -> mark_failed -> END
```

The graph has a maximum of three repair attempts and a fixed recursion limit. Exhaustion produces
`failed_compilation`, never an inferred covenant.

## 9. Covenant registry and temporal resolution

DuckDB stores documents, blocks, document-borrower mappings, aliases, covenants, borrowers,
versions, filters, results, calculations, and pipeline stage records.

`TemporalResolver.resolve(covenant_group_id, borrower_id, at_date)` selects exactly one version
whose effective interval includes the date. Zero matches is explicit `not_effective`; multiple
matches is `overlapping_versions`. Neither case silently chooses a rule.

## 10. Deterministic evaluator extensions

The registry adds:

- `RatioEvaluator` with zero-denominator semantics and optional maximum group ratio;
- `ExistenceEvaluator`, implemented as an exact match count compared with zero;
- `FrequencyEvaluator`, implemented as the maximum grouped count;
- group borrower scope using `borrower_id IN (...)`;
- exclusions compiled as negated, parameterized filters;
- explicit configured transaction date field;
- currency guard that rejects mixed-currency money aggregation without an FX policy.

Evaluators preserve empty-set semantics:

```text
SUM=0, COUNT=0, EXISTENCE=0
MAX/MIN/AVG/RATIO/FREQUENCY-without-buckets=undefined unless the rule defines zero semantics
```

## 11. Evidence selection

Evidence selectors are independent components:

- maximum transaction: highest value with deterministic tie-breaking;
- violating transaction: first or strongest transaction that independently violates the rule;
- trigger transaction: row `limit + 1` after ordering by transaction date and transaction ID;
- existence evidence: first deterministic prohibited match;
- frequency evidence: transaction that crosses the maximum bucket limit.

Every selected transaction is rechecked for existence, borrower scope, date window, filters, and
its claimed violating or triggering relationship. Evidence failure preserves correct number and
verdict and changes status to `partial`.

## 12. Verification and repair graph

The deterministic verifier checks completeness, calculation reproduction, numeric type/unit,
currency, comparator/verdict, temporal version, and evidence.

Issues are classified:

- `ok`;
- `repairable_spec`;
- `repairable_mapping`;
- `repairable_evidence`;
- `non_repairable`.

`RepairState` contains the original spec, immutable transaction snapshot hash, verification
issues, repair proposal, attempt count, reevaluated result, and verification report.

Graph nodes:

```text
classify -> propose_repair -> validate_patch -> reevaluate -> reverify -> route
route repairable -> propose_repair
route ok -> END
route exhausted/non_repairable -> END
```

Only CovenantSpec, borrower mapping, period mapping, and evidence strategy may be patched. A repair
cannot mutate transactions, write number or verdict, bypass the comparator, or skip deterministic
reevaluation. The graph has at most two repair attempts.

## 13. LangSmith observability

`@traceable` decorates the four root workflows and all public stages:

```text
pipeline.preprocess
  document.classify_page, pdf.extract_native, ocr.paddle_gpu,
  vlm.paddle_layout, retrieval.index/search, borrower.resolve,
  covenant.detect/compile, covenant.registry.save

pipeline.evaluate
  transaction.ingest, temporal.resolve, sql.build,
  evaluator.calculate, comparator.compare, evidence.select,
  calculation.persist

pipeline.verify
  completeness.verify, number.reproduce, verdict.reproduce,
  evidence.verify, temporal.verify, repair.graph

pipeline.serialize
  submission.map, submission.validate
```

Stage metadata includes run/document/page/borrower/covenant/calculation IDs, component versions,
dataset hash, latency, retry count, and status. SQL traces include the template, safe parameter
summary, bounds, row count, and result.

`LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT` enable remote tracing.
`tracing.payload_mode=redacted` is the default and excludes full PDFs, raw transaction rows, and
secrets. `full` is allowed for synthetic data. Nested trace context is propagated automatically.
Remote trace failures create local warnings and never affect evaluation.

## 14. Batch pipeline and CLI

Commands:

```text
preprocess INPUT --at-date DATE
inspect-covenants
evaluate-all --at-date DATE
verify-results --run-id ID
serialize-submission --profile PROFILE
validate-submission FILE --profile PROFILE
benchmark-full
ocr-smoke FILE
```

`preprocess` is content-hash idempotent. `evaluate-all` constructs the completeness matrix and
isolates every borrower/covenant pair. Each command produces machine-readable artifacts and a
non-zero exit code for invalid inputs or failed acceptance thresholds.

## 15. Submission serializer

Internal results remain richer than submission output. A submission profile defines key names,
verdict mapping, numeric representation, percentage convention, null behavior, evidence shape,
ordering, and extra-field policy. A strict Pydantic schema validates the serialized file. The
official profile is added without changing evaluator internals once the official template exists.

## 16. Testing strategy

Tests use dependency injection and fake LangChain models; normal test runs make no network calls.

Coverage includes:

- native, scanned, low-quality, and table PDF routing;
- Cyrillic OCR and CPU fallback;
- multi-rule splitting and ambiguous compiler loops;
- exact/fuzzy/ambiguous borrower resolution;
- temporal amendments and overlapping-version rejection;
- ratio, existence, frequency, group scope, exclusions, mixed currency, and empty sets;
- all evidence selectors and deterministic tie-breaking;
- verifier corruption and repair authorization boundaries;
- LangGraph attempt and recursion limits;
- nested `@traceable` stage topology with a recording test client;
- strict serializer profiles and golden files;
- end-to-end PDF + XLSX to submission candidate;
- existing evaluator benchmark regression;
- optional live DeepSeek, GPU OCR, and LangSmith smoke tests.

## 17. Definition of Done

- Native PDF and GPU/CPU OCR routes produce provenance-preserving blocks.
- Complex table pages use the local visual parser.
- DeepSeek compilation runs through LangChain structured output.
- Straightforward clauses avoid LangGraph.
- Ambiguous clauses use a bounded compiler graph.
- All supported metric types execute deterministically.
- Temporal and borrower/group scope are enforced.
- Evidence selectors return and verify the intended transaction.
- Batch evaluation never loses a borrower/covenant pair.
- Repair graph cannot alter transactions, number, comparator, or verdict directly.
- Every named pipeline stage appears as a nested trace when LangSmith tracing is enabled.
- Evaluation succeeds when LangSmith is disabled or unavailable.
- Submission candidates validate against a strict selected profile.
- CPU test suite, deterministic benchmark, Docker build, GPU OCR smoke, and opt-in DeepSeek smoke
  pass with documented commands.

## 18. Known external dependency

Exact official submission keys and scoring representation cannot be inferred before the organizer
publishes the template. The implementation therefore delivers a strict configurable serializer
and synthetic profile, not a fabricated official profile.
