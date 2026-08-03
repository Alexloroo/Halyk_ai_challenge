# Codex-2 Review Workflow

`codex-2` adds a quality-review layer after deterministic covenant evaluation and before final reporting/submission preparation.

The authoritative covenant result is still produced by DuckDB/Python. The reviewer does not become the arithmetic source of truth.

## Data flow

```text
PDF / structured input
  -> CovenantSpec + canonical transactions
  -> deterministic evaluation
  -> BatchEvaluationReport
  -> ReviewPipeline
       -> question + answer + deterministic rationale
       -> first LLM review
       -> confidence >= 0.70: accept review
       -> confidence < 0.70 / verifier issue / partial result / low compiler confidence
            -> embed current question/rule
            -> cosine top-K validated cases
            -> second LLM review
       -> validate reviewer against deterministic answer
  -> ReviewedBatchReport
  -> inspect / LangSmith / quality gate
  -> SubmissionSerializer still uses authoritative CovenantResult values
```

## Why similarity cannot copy answers

A semantically similar covenant question can belong to another borrower and have a completely different numeric result.

Therefore similar cases are used only as reasoning-pattern examples. The review validator enforces:

- reviewer `number` must equal the current deterministic `CovenantResult.number`;
- reviewer evidence may only be the current verified evidence transaction or `null`;
- reviewer verdict must agree with the current deterministic number, comparator and threshold;
- an illegal reviewer output is marked `invalid_reviewer_output` and the deterministic result is retained.

## Confidence/fallback policy

Default thresholds:

```text
review confidence threshold:     0.70
compiler confidence threshold:   0.70
similarity top-K:                5
minimum cosine similarity:       0.55
```

Similarity fallback is triggered when any condition is true:

```text
first review confidence < 0.70
OR first reviewer rejects the answer
OR deterministic verifier has an issue
OR CovenantResult.status != success
OR compiler confidence < configured threshold
```

LLM confidence is not treated as a calibrated probability; it is only one trigger signal.

## Review corpus format

The review corpus must contain validated/golden cases only. Do not automatically add every model answer to this file.

Example `review_cases.json`:

```json
[
  {
    "case_id": "gold-monthly-sum-1",
    "question": "Нарушен ли месячный лимит исходящих платежей?",
    "covenant_type": "financial",
    "metric_type": "sum",
    "answer": {
      "borrower_id": "B-GOLD",
      "covenant_id": "COV-GOLD",
      "verdict": "violated",
      "number": "16000000",
      "number_unit": "KZT",
      "evidence_transaction_id": null,
      "calculation_id": null,
      "status": "success",
      "failure_stage": null,
      "errors": []
    },
    "rationale": "Monthly outgoing KZT total is 16M; covenant threshold is <=15M.",
    "embedding_text": "monthly outgoing KZT sum covenant limit"
  }
]
```

Recommended corpus sources:

- curated synthetic golden examples;
- official/public examples after publication;
- manually verified failure/regression cases;
- externally validated previous cases.

## Organizer question format

An optional questions file lets the reviewer receive the exact competition question instead of an automatically generated technical question.

Example `questions.json`:

```json
[
  {
    "borrower_id": "B001",
    "covenant_id": "COV-ALPHA-SUM",
    "question": "Соблюдён ли месячный лимит исходящих платежей за апрель?"
  }
]
```

Duplicate `(borrower_id, covenant_id)` entries are rejected.

## Installation

The deterministic project dependencies are installed normally:

```bash
pip install -e '.[dev]'
```

The live cosine fallback uses the local SentenceTransformer provider and therefore requires the semantic extra:

```bash
pip install -e '.[semantic]'
```

The default model is:

```text
intfloat/multilingual-e5-small
```

The embedding provider is injectable, so a different local/API provider can be used later without changing `ReviewService`.

## Runtime

First run the existing deterministic pipeline:

```bash
halyk-covenants preprocess ./data/private \
  --db data/duckdb/hackathon.duckdb \
  --ocr

halyk-covenants evaluate-all \
  --at-date 2026-04-30 \
  --db data/duckdb/hackathon.duckdb \
  --output data/internal-results.json
```

Then run review:

```bash
halyk-review review-results \
  --results data/internal-results.json \
  --review-corpus data/review_cases.json \
  --questions data/questions.json \
  --db data/duckdb/hackathon.duckdb \
  --output data/reviewed-results.json
```

Optional tuning:

```bash
halyk-review review-results \
  --results data/internal-results.json \
  --review-corpus data/review_cases.json \
  --db data/duckdb/hackathon.duckdb \
  --output data/reviewed-results.json \
  --confidence-threshold 0.70 \
  --compiler-confidence-threshold 0.70 \
  --top-k 5 \
  --minimum-similarity 0.55 \
  --embedding-model intfloat/multilingual-e5-small
```

`DEEPSEEK_API_KEY` is required for the live reviewer. The semantic model is loaded lazily only when embedding is actually requested.

## Reviewed report

Every reviewed case contains:

```text
result                  authoritative CovenantResult
review                  structured LLM ReviewDecision
review_status            accepted / accepted_after_similarity /
                         low_confidence / invalid_reviewer_output / review_failed
fallback_reasons[]       deterministic reason(s) fallback was triggered
similarity_scores{}      case_id -> cosine score calculated by Python
```

Review decisions are also persisted separately in DuckDB table `review_decisions`.

Review metadata does not alter `covenant_results`.

## LangSmith

The review path exposes business spans:

```text
pipeline.review
  review.case
    review.rationale (deterministic construction is represented in case context)
    review.first_pass
      review.llm
    review.similarity.embed
    review.similarity.search
    review.second_pass
      review.llm
    review.validate
```

Useful metadata includes:

- borrower/covenant IDs;
- first-pass confidence;
- fallback reasons;
- retrieved case IDs;
- cosine scores;
- final review status;
- reviewer model/prompt version.

No hidden model chain-of-thought is stored.

## Important submission rule

`reviewed-results.json` is a quality/debug artifact.

The official submission serializer still consumes authoritative deterministic result fields. A reviewer cannot silently rewrite number/evidence in the submitted answer.

If a review exposes a real deterministic problem, fix or route that case through a bounded deterministic repair path, re-run evaluation, and then review the new result.
