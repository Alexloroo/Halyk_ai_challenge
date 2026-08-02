# Codex-1 implementation notes

This file is the working source of truth for the `codex-1` branch. It consolidates the architecture changes and MVP requirements discussed after the original covenant MVP design.

## Branch safety

- Work only on `codex-1`.
- Do not merge into `main`.
- Keep the final state on `codex-1` for manual review.

## Competition contract

The submission is JSON using the organizer-provided template once available.

Every `(borrower, covenant)` result is independently scored and has three independently valuable components:

1. verdict: covenant complied / violated;
2. exact numeric value supporting the verdict;
3. evidence transaction when the violation is tied to a concrete transaction.

Partial credit is possible, so a failure to resolve evidence must not discard a correct number/verdict.

The internal result schema must remain richer than the official submission schema. Official field names and normalization conventions belong only in the submission serializer.

## Architectural center

This is a covenant compilation and deterministic evaluation system, not a generic autonomous agent.

```text
PDF / structured docs
  -> native parse / OCR / visual parse
  -> borrower + covenant discovery
  -> LLM Covenant Compiler
  -> validated CovenantSpec registry

CSV/XLSX/Parquet
  -> typed normalization
  -> DuckDB transactions

CovenantSpec + DuckDB
  -> deterministic evaluator
  -> number
  -> comparator
  -> verdict
  -> evidence selector
  -> verifier
  -> strict submission serializer
```

The hot evaluation path must not depend on an agent deciding which arithmetic tool to call.

## Deterministic core

Plain Python + DuckDB own:

- transaction normalization;
- money using `Decimal` / DuckDB `DECIMAL`, never float;
- IDs as strings;
- filters;
- time windows;
- SUM / COUNT / MAX / MIN / AVG / RATIO / EXISTENCE / FREQUENCY calculations;
- comparator semantics including exact boundary behavior;
- temporal covenant version resolution;
- evidence transaction validation;
- verdict calculation;
- completeness checks;
- final JSON serialization.

LLM output is never the numeric truth when the source value can be calculated deterministically.

## LangChain boundary

Use LangChain as an integration layer, not as the domain architecture.

LangChain is appropriate for:

- model abstraction;
- prompt templates;
- Pydantic structured output;
- covenant compilation;
- semantic classification/detection;
- embeddings/retrieval adapters;
- typed tool wrappers around domain services.

Do not wrap DuckDB, evaluator implementations, comparator logic, temporal resolution or verifier business logic in chains/runnables merely for consistency.

Domain services must be callable without LangChain.

## LangGraph policy

LangGraph is optional and only justified for bounded repair loops/stateful workflows, for example:

- covenant compile -> validate -> retrieve missing definition -> recompile;
- ambiguous entity -> retrieve more evidence -> resolve again;
- verification -> missing required evidence -> evidence repair -> verify again.

Simple deterministic fallbacks such as native PDF -> OCR -> VLM can remain ordinary Python control flow.

All loops must be bounded. No unbounded agent autonomy.

## PaddleOCR / document parsing

Preferred MVP document routing:

```text
PDF page
  -> usable native text? -> PyMuPDF
  -> otherwise OCR/layout path
       -> PaddleOCR / PP-Structure-style provider
       -> visual/VLM fallback only for hard pages
```

OCR is page-level fallback, not the default for all PDFs.

Tables require three representations:

1. raw visual/source evidence;
2. structured rows for DuckDB/calculation;
3. semantic summary for retrieval only.

Table summaries must never be used as numeric truth.

OCR/VLM providers stay behind interfaces so the provider can be replaced after public-dataset benchmarking.

Current environment limitation: DeepSeek API and real PaddleOCR execution are not available to Codex in this implementation session. Keep these adapters production-shaped and covered by interface/contract tests where possible, but do not claim live provider tests.

## Borrower and temporal resolution

Support:

- one PDF containing multiple borrowers;
- one covenant applying to one or many borrowers;
- exact identifiers before fuzzy names;
- aliases and fuzzy matching only after exact identifiers;
- LLM adjudication only for ambiguous candidates;
- `effective_from` / `effective_to` covenant versions;
- amendments that change a rule during the monitored period;
- group-level covenants as an extension point.

## CovenantSpec / DSL

The compiler must output a typed machine-readable rule, not prose.

A covenant must preserve at least:

- borrower scope;
- metric type;
- field;
- filters/exclusions;
- group-by where needed;
- time window/date field;
- comparator;
- threshold;
- unit/currency;
- evidence mode;
- effective dates;
- source provenance;
- compiler confidence/metadata.

Independent conditions inside one paragraph must be split into independent covenant rules.

## Partial result behavior

Each borrower/covenant pair is evaluated independently.

Status should distinguish success / partial / failed. A missing evidence transaction must not erase a correct numeric result or verdict.

## LangSmith observability

LangSmith is part of the MVP debugging workflow, not a production afterthought.

Trace meaningful business stages, including ordinary Python functions, with nested spans such as:

```text
evaluate_covenant
  parse_document
  detect_covenants
  compile_covenant
  resolve_borrower
  resolve_covenant_version
  calculate_metric
    build_filters
    build_sql
    duckdb_execute
  compare_threshold
  select_evidence_transaction
  verify_result
  serialize_result
```

Prefer business names over framework names such as `RunnableSequence`.

Root/span metadata should include when available:

- run/case ID;
- borrower ID;
- covenant ID/type;
- pipeline version;
- compiler model/prompt version;
- parser/OCR/retrieval strategy;
- dataset/split;
- failure stage;
- latency and token metadata where provided.

Tracing must not fail business execution when disabled or unavailable.

Do not send secrets, raw auth data, hidden reasoning, complete production PDFs or huge transaction row dumps to traces. Store diagnostic summaries/IDs instead.

## Evaluation strategy

Do not use a single vague quality score.

Create/maintain separate golden datasets/evaluators for:

1. covenant detection
   - recall is the priority;
   - precision;
   - independent-rule split accuracy;

2. covenant compiler
   - metric type;
   - field;
   - filters;
   - period/date field;
   - comparator;
   - threshold;
   - currency/unit;
   - borrower scope;
   - evidence mode;

3. deterministic covenant execution
   - exact number;
   - exact verdict;
   - exact evidence transaction;

4. end-to-end
   - hackathon-like component score;
   - submission completeness/validity;

5. regression failures
   - every important manual failure found during debugging should become a permanent fixture.

Use code evaluators for exact fields. LLM-as-judge is only appropriate for genuinely semantic/fuzzy checks.

## Error taxonomy

Classify failures by stage where possible:

- OCR/parsing;
- covenant detection;
- covenant compilation;
- borrower resolution;
- temporal resolution;
- query/filter generation;
- deterministic calculation;
- verdict/comparator;
- evidence selection;
- serialization.

This taxonomy should be surfaced in trace metadata and local evaluation reports.

## Synthetic benchmark target

The MVP should support a local synthetic end-to-end benchmark containing at least:

- native text PDF;
- image-only/scanned PDF fixture/interface path;
- table-oriented multi-borrower document;
- amendment changing a covenant effective mid-period;
- aggregate SUM covenant without evidence transaction;
- transaction-level MAX covenant with evidence;
- COUNT boundary case;
- RATIO covenant;
- prohibited counterparty/existence covenant;
- weekend/date condition;
- explicit gold submission/results for regression comparison.

Synthetic output conventions may be local, but must be isolated from the future official serializer.

## Testing expectations for this branch

Test every feature that can run without external DeepSeek/OCR infrastructure.

Must cover at minimum:

- domain validation;
- DuckDB ingestion/typing;
- evaluator families;
- comparator boundaries;
- time windows;
- borrower resolution precedence;
- temporal covenant versions;
- evidence selection/validation;
- partial result behavior;
- retrieval with deterministic/fake embeddings;
- native PDF path;
- tracing disabled/failure-safe behavior;
- LangSmith evaluation helper logic without requiring network;
- synthetic benchmark scoring;
- strict serializer validation using local synthetic schema;
- CLI/integration paths that do not require external providers.

DeepSeek and real OCR calls are explicitly excluded from live tests in this session. Keep them injectable and test their surrounding contracts with fakes/mocks where possible.

## Implementation priority

1. Preserve deterministic covenant evaluator correctness.
2. Add complete, useful observability/evaluation surfaces.
3. Add the synthetic benchmark and golden comparison workflow.
4. Keep LangChain at the AI integration boundary.
5. Add LangGraph only where a bounded repair loop materially improves correctness.
6. Avoid infrastructure that does not improve hackathon score/debug speed.
