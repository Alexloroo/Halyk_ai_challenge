# 06 — codex-1 vs codex-2

> Read [03_BRANCH_ANALYSIS.md](03_BRANCH_ANALYSIS.md) first.
> These are not competing designs. `codex-2 = codex-1 + review layer`, with zero modification to the
> deterministic pipeline. This comparison is therefore **"baseline"** vs **"baseline + layer"**.

---

## 1. Scope of the difference

| | codex-1 | codex-2 |
| --- | --- | --- |
| Ingestion → blocks | identical | identical |
| Borrower resolution | identical | identical |
| Detection | identical | identical |
| Compilation + repair | identical | identical |
| Registry / identity | identical | identical |
| SQL building | identical | identical |
| Evaluators | identical | identical |
| Temporal resolution | identical | identical |
| Evidence + validation | identical | identical |
| Verifier | identical | identical |
| Serializer | identical | identical |
| **Review layer** | absent | **present** |
| CLI entrypoints | `halyk-covenants` | `halyk-covenants` + `halyk-review` |

Lines changed in the deterministic pipeline between the two branches: **0**.

---

## 2. Head-to-head

| Dimension | codex-1 | codex-2 | Assessment |
| --- | --- | --- | --- |
| **Correctness** | Deterministic; verified only against itself | **Identical** | Tie by construction — the delta cannot change an answer |
| **Complexity** | ~7,900 LOC, 13 packages | ~9,600 LOC, 15 packages (+22%) | codex-1 |
| **LLM dependency** | 2 call sites (compile, repair), preprocessing only | 4 call sites (+2 review passes), now also **post-evaluation** | codex-1 |
| **Latency** | 1–3 LLM calls per *covenant clause*, once | + 1–2 calls per *result*, every run | codex-1 |
| **Cost** | Compilation is cacheable via SHA-256 idempotency | Review re-runs every time; cost scales with pairs × runs | codex-1 |
| **Verification** | Comparator re-check + evidence re-derivation + (tautological) completeness | Same, plus an LLM second opinion on a full spec+rationale payload | codex-2 |
| **Fallback** | Partial results, `FailureStage`, bounded compiler repair | Same, plus similarity-assisted second review pass | codex-2 (marginally) |
| **Testability** | Fully deterministic; every path unit-testable offline | Review path needs stubs for both LLM and embedder; both are injected, so it is testable — but the *live* path needs an unlisted extra | codex-1 |
| **Debugging** | LangSmith spans, `Calculation` provenance, `FailureStage` | Same, plus `review_status`, `fallback_reasons`, `similarity_scores`, persisted reviewer metadata | **codex-2** |
| **Hackathon usefulness** | Ships and scores | Adds no score; adds runtime risk (extra dependency, extra API calls) and one genuinely useful triage artifact | **codex-1 for the run, codex-2 for the write-up** |

## 3. The decisive table

Because the review layer is cage-constrained and its report is a fork, not a chain:

| Question | Answer |
| --- | --- |
| Can codex-2 produce a different `verdict` than codex-1? | **No** |
| Can codex-2 produce a different `number`? | **No** |
| Can codex-2 produce a different `evidence_transaction_id`? | **No** |
| Can codex-2 produce a different `submission.json`? | **No** |
| Can codex-2 tell you *which answers to distrust*? | **Yes** — this is its entire value |
| Does anything consume that signal automatically? | **No** |

Proof sketch: `SubmissionSerializer.serialize` takes `list[CovenantResult]`;
`ReviewedBatchReport.authoritative_results` returns `[item.result for item in self.reviewed_results]`
where each `item.result` is the input `CovenantResult` object, never reassigned; and
`ReviewService._validate_decision` rejects any decision whose number, evidence or verdict differs from
the deterministic answer. The three scored fields are pinned on every path, including the failure
paths (`_fallback_decision` copies them verbatim from `case.answer`).

## 4. Cost model

Let `C` = compiled covenants, `P` = (borrower, covenant) pairs, `R` = number of evaluation runs.

| | codex-1 | codex-2 |
| --- | --- | --- |
| LLM calls, preprocessing | `C … 3C` (compile + ≤2 repairs), **once** (SHA-256 idempotent) | same |
| LLM calls, evaluation | `0` | `P … 2P` **per run** |
| Extra model downloads | none | `intfloat/multilingual-e5-small` (~120 MB) on first fallback |
| Extra install surface | none | `semantic` extra (sentence-transformers + faiss-cpu) |
| Failure blast radius | a bad compile costs one covenant | a missing extra costs the whole review run (not the submission) |

For a run with 40 pairs and 3 iterations, the review layer costs ~120–240 additional LLM calls that
cannot change the submitted answer by even one digit.

## 5. What each branch is actually good at

**codex-1 is the product.** It is a coherent, defensible, deterministic system with a real LLM
boundary and real fault isolation. Its weaknesses are all in *verification of the interpretation
stage* — the checks it has are strong but pointed at execution, where errors are already loud.

**codex-2 is the diagnostic.** It correctly identifies that the interpretation stage is where trust
is missing, assembles exactly the right payload to interrogate it (raw clause + compiled spec +
deterministic rationale + verification issues + compiler confidence), and then writes the answer to a
side file.

The two are complementary in intent and disconnected in implementation. That gap is the opportunity.

## 6. Recommendation

```text
Base for all further work:   codex-2   (strict superset; nothing is lost)

For a submission run today:  run codex-1's pipeline path only
                             (halyk-covenants preprocess → evaluate-all → serialize-submission)
                             and skip halyk-review — it costs calls and changes nothing

For architecture-v3:         keep the reviewer's inputs and its cage
                             move it from "review the answer" to "review the spec"
                             wire rejection into bounded re-compilation
                             drop the similarity corpus until it is shown to pay for itself
```

The single highest-leverage change available is not adding anything new — it is **connecting the
signal codex-2 already computes to the pipeline codex-1 already has.** See
[09_ARCHITECTURE_V3.md](09_ARCHITECTURE_V3.md).

---

Next: [07_FINDINGS.md](07_FINDINGS.md)
