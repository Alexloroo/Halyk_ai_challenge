# 00 — Project Context

> Baseline context for the architecture-v3 research track.
> Everything here is derived from the repository itself (`main`, `codex-1`, `codex-2`), not from external assumptions.

Related: [01_TASK_MODEL.md](01_TASK_MODEL.md) · [02_REPOSITORY_MAP.md](02_REPOSITORY_MAP.md) · [README.md](README.md)

---

## Project purpose

The repository implements a **covenant evaluation system** for the Halyk Agentic Challenge.

Given a set of loan/credit documents (PDF) and a set of borrower transactions (CSV/XLSX/Parquet), the
system must decide, for every `(borrower, covenant)` pair, whether the borrower **complied with** or
**violated** the covenant, and support that verdict with a number and — where applicable — a specific
transaction.

The controlling design statement lives in [`docs/2_ARCHITECTURE_COVENANT_MVP.md`](../2_ARCHITECTURE_COVENANT_MVP.md) §0:

```text
LLM interprets the covenant text.
DuckDB/Python executes the covenant.
Verifier checks the result.
Serializer produces exactly the required JSON.
```

The LLM is explicitly **not** the calculator. This is the single most important architectural
commitment in the codebase, and both `codex-1` and `codex-2` honour it.

---

## Competition / task understanding

From `docs/2_ARCHITECTURE_COVENANT_MVP.md` §0 and §1:

- every borrower has one or more covenants;
- every covenant is evaluated **independently**;
- output per `(borrower, covenant)` pair contains three scored components:
  1. **verdict** — complied / violated;
  2. **number** — the numeric value supporting the verdict;
  3. **evidence transaction** — when the violation is tied to a specific transaction;
- **partial credit is possible per answer component**;
- the final score is the **sum across all covenants**.

The scoring shape drives the architecture. Because credit is per-component and additive:

| Consequence | Implication for design |
| --- | --- |
| A wrong number can still earn verdict credit | Never abandon a pair; always emit something |
| A crash on one covenant must not lose the others | Fault isolation is worth more than global elegance |
| Number accuracy dominates | Correct filters/windows matter more than clever agents |
| Evidence is a separate component | Evidence selection deserves its own logic and its own tests |

The declared optimization order is `NUMBER → VERDICT → EVIDENCE`, with the stated bottleneck being
**covenant text → correct machine-readable rule → correct filtered transaction set → correct number**.

---

## Inputs

| Input | Format | Location in repo | Notes |
| --- | --- | --- | --- |
| Loan/covenant documents | PDF (native text + scanned) | `data/raw/documents/` | 4 fixtures incl. a scan and a portfolio table |
| Transactions | CSV / XLSX / Parquet | `data/raw/transactions/transactions.csv` | Loaded into DuckDB |
| Borrower registry | CSV | `data/raw/transactions/borrowers.csv` | Feeds entity resolution |
| Organizer questions (optional) | JSON | user-supplied | `codex-2` only; maps `(borrower, covenant) → question` |
| Golden review corpus (optional) | JSON | user-supplied | `codex-2` only; similarity fallback examples |

Supported structured suffixes are a closed set: `.csv`, `.xlsx`, `.xlsm`, `.parquet`
(`pipeline/preprocess.py:23`).

## Outputs

| Output | Producer | Purpose |
| --- | --- | --- |
| `submission.json` | `submission/serializer.py` | **The scored artifact** |
| `internal-results.json` | `pipeline/evaluate.py` | Full `BatchEvaluationReport` incl. verification |
| `reviewed-results.json` | `pipeline/review.py` (`codex-2`) | Quality/debug artifact — **not** scored |
| DuckDB database | `storage/duckdb_store.py` | Durable state: transactions, covenants, results, provenance |
| LangSmith traces | `observability/` | Stage-level spans and metadata |

The submission shape is configurable through a `SubmissionProfile` (`submission/models.py`) so the
official template can be adapted without touching the pipeline — key names, verdict labels,
ratio-as-percentage, and evidence inclusion are all profile fields.

---

## Core entities

Defined in `src/halyk_covenants/domain/`:

| Entity | File | Role |
| --- | --- | --- |
| `SourceRef` | `domain/source.py` | Document/page/bbox provenance carried end-to-end |
| `DocumentBlock` | `domain/document.py` | One extracted text/table unit with bbox + confidence |
| `CovenantCandidate` | `covenants/detector.py` | Pre-LLM detected clause |
| **`CovenantSpec`** | `domain/covenant.py` | **The central object** — the executable rule |
| `MetricSpec` | `domain/covenant.py` | What to compute (sum/count/max/min/avg/ratio/existence/frequency) |
| `ConditionSpec` | `domain/covenant.py` | Comparator + threshold + unit/currency |
| `FilterSpec` | `domain/covenant.py` | One closed-catalog predicate |
| `TimeWindowSpec` | `domain/covenant.py` | Calendar/rolling/custom window |
| `EvidenceMode` | `domain/covenant.py` | none / violating / trigger / max transaction |
| `Calculation` | `domain/calculation.py` | Deterministic provenance record (SQL + params + row count) |
| `CovenantResult` | `domain/result.py` | verdict + number + evidence + status + failure stage |
| `ReviewDecision` / `ReviewedResult` | `review/models.py` | `codex-2` only |

`CovenantSpec` is the contract between the LLM half and the deterministic half of the system. Every
architectural question in this repository ultimately reduces to *"is this spec right, and can we tell?"*

---

## External models and services

| Service | Used for | Configured via | Required? |
| --- | --- | --- | --- |
| **DeepSeek** (via `langchain-deepseek`) | Covenant compilation, compiler repair, `codex-2` review | `DEEPSEEK_API_KEY` | Yes for compilation |
| **LangSmith** | Tracing / evaluation | `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | No (degrades to no-op) |
| **PaddleOCR / PP-Structure** | Scanned pages, table layout | `ocr` extra | No (optional extra) |
| **SentenceTransformers** (`intfloat/multilingual-e5-small`) | `codex-2` review similarity | `semantic` extra | No (optional extra) |
| **LangGraph** | Compiler repair loop state machine | dependency | Yes |

Note that **only DeepSeek is genuinely required**. OCR and embeddings are optional extras and are
*not installed in CI* — see [07_FINDINGS.md](07_FINDINGS.md).

## Storage

Single-file **DuckDB** database (default `data/duckdb/hackathon.duckdb`). Schema in
`storage/duckdb_store.py`:

```text
raw_transactions          documents              covenants
transactions              document_blocks        covenant_borrowers
borrowers                 calculations           covenant_results
borrower_aliases          pipeline_stage_records covenant_result_history
borrower_identifiers      ingestion_artifacts    review_decisions (codex-2)
```

There is **no vector database**. The `1_ARCHITECTURE` document proposes Qdrant; the implemented
system uses an in-process BM25 + optional numpy cosine hybrid instead. This is a deliberate and, in
our assessment, correct simplification for the data volume involved.

---

## Unknowns

These are genuinely not determinable from the repository and are carried as open questions:

1. **The official submission schema.** `configs/submission/synthetic.yaml` is a synthetic profile.
   The real key names, verdict vocabulary, and number formatting are unknown.
2. **The real document corpus.** All four PDFs under `data/raw/documents/` are synthetic fixtures.
   Real-world layout complexity, language mix, and scan quality are unmeasured.
3. **Whether covenants are given or must be discovered.** The pipeline discovers covenants from PDFs;
   if the organizers supply a covenant list, the entire detection + compilation stage becomes
   optional and the risk profile changes completely.
4. **Language distribution.** The code handles Russian and English. Kazakh is not handled anywhere.
5. **Evaluation date semantics.** `--at-date` is operator-supplied; whether the organizers define a
   single as-of date or per-covenant periods is unknown.
6. **Scoring tolerance for numbers.** Exact match vs. relative tolerance materially changes whether
   currency/rounding work is worth doing.

## Important assumptions

Assumptions this research track makes explicit, so they can be challenged:

- **A1.** The submission is scored per component (verdict / number / evidence) and summed. Everything
  in [09_ARCHITECTURE_V3.md](09_ARCHITECTURE_V3.md) is optimized against this.
- **A2.** Compilation (text → `CovenantSpec`) is the dominant error source, not SQL execution. The
  deterministic half is small, closed, and testable; the LLM half is open-ended.
- **A3.** Latency is not directly scored, but a pipeline that cannot finish is worth zero. Wall-clock
  budget is treated as a hard constraint, not an optimization target.
- **A4.** Recall at detection is unrecoverable downstream. A clause never detected can never be
  compiled, evaluated, or scored — no later stage can repair it.
- **A5.** The graders will not run the OCR or semantic extras unless we ship them working by default.

---

Next: [01_TASK_MODEL.md](01_TASK_MODEL.md)
