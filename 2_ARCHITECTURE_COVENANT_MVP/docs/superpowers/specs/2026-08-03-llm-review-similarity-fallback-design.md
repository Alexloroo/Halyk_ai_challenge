# Codex-2 — LLM Review + Similarity Fallback Design

## Goal

Add a final review layer immediately before reporting/submission. The layer receives the current question, deterministic answer, rationale, calculation/evidence context and asks an LLM to assess whether the answer is trustworthy.

If review confidence is below `0.70`, or deterministic verification already reports a problem, the system retrieves semantically similar historical/golden cases using embeddings + cosine similarity and asks the reviewer to reconsider the current result with those examples as reference.

The reviewer is a verification/fallback layer, not the owner of arithmetic truth.

## Core safety rule

The review layer must **not invent a new numeric answer from semantic similarity**.

Similarity retrieves analogous problem-solving patterns and known examples. It does not copy another borrower's number, verdict or transaction into the current case.

For the current case:

- deterministic `number` remains sourced from the current `Calculation`;
- current `evidence_transaction_id` must refer to a transaction from the current dataset and pass the existing evidence verifier;
- a reviewer may accept the current result or reject/flag it;
- a reviewer may recommend an alternate verdict only when it is derivable from the current deterministic number + covenant comparator/threshold;
- a reviewer cannot create an arbitrary number that is absent from the current deterministic calculation path.

This prevents a highly similar question for another borrower from contaminating the answer.

## Placement in pipeline

```text
CovenantSpec + DuckDB
        |
        v
Deterministic Evaluator
        |
        +--> number
        +--> verdict
        +--> evidence transaction
        |
        v
Existing Verifier
        |
        v
ReviewCase Builder
        |
        v
LLM Reviewer
        |
        +-----------------------------+
        |                             |
 confidence >= 0.70              low confidence / issue
        |                             |
        v                             v
      ACCEPT                  Similarity Retriever
                                      |
                                      v
                             question embeddings
                                      |
                                      v
                             cosine top-K cases
                                      |
                                      v
                               LLM Re-review
                                      |
                                      v
                               ReviewDecision
        |                             |
        +--------------+--------------+
                       |
                       v
                ReviewedResult
                       |
                       v
             Submission Serializer
```

## Trigger policy

Similarity fallback triggers if **any** of the following is true:

```text
review_confidence < 0.70
OR deterministic verifier reports an issue
OR CovenantResult.status != success
OR compiler confidence is below configured threshold
```

LLM self-confidence is treated as one signal, not calibrated probability.

Default threshold:

```yaml
review:
  confidence_threshold: 0.70
```

## ReviewCase

```python
class ReviewCase(BaseModel):
    case_id: str

    borrower_id: str
    covenant_id: str
    evaluation_date: date

    question: str

    answer: CovenantResult

    rationale: str

    covenant: CovenantSpec

    calculation: Calculation | None = None

    verification_issues: list[str] = []

    compiler_confidence: Decimal | None = None
```

`rationale` is not hidden chain-of-thought. It is a compact, inspectable evidence summary produced from deterministic artifacts, for example:

```text
April outgoing KZT total = 16,000,000 from 3 matched transactions.
Covenant requires total <= 15,000,000 KZT.
16,000,000 > 15,000,000, therefore violated.
This is an aggregate covenant, so no single evidence transaction is required.
```

## Deterministic rationale builder

Do not ask the first reviewer model to invent the explanation from scratch.

Build a concise rationale from:

- raw covenant text;
- metric type;
- filters;
- time window;
- effective dates;
- calculation value;
- threshold;
- comparator;
- row count;
- evidence transaction when present.

This makes reviewer input auditable and keeps the model focused on checking rather than recomputing.

## ReviewDecision

```python
class ReviewDecision(BaseModel):
    accepted: bool
    confidence: Decimal

    verdict: Literal["complied", "violated", "unknown"]
    number: Decimal | int | None
    evidence_transaction_id: str | None

    rationale: str
    issues: list[str] = []

    used_similarity_fallback: bool = False
    similar_case_ids: list[str] = []
```

Validation rules:

1. `confidence` must be `0 <= x <= 1`.
2. Reviewer number must equal the current deterministic number unless explicitly null because the deterministic result itself is unavailable.
3. Reviewer evidence transaction must either equal a verified current evidence transaction or be null.
4. Reviewer verdict must match deterministic comparator semantics when number and threshold are available.
5. A response violating these constraints is rejected and converted into a review failure instead of silently overriding the result.

## Similarity case model

```python
class SimilarReviewCase(BaseModel):
    case_id: str
    question: str

    covenant_type: str | None = None
    metric_type: str | None = None

    answer: CovenantResult
    rationale: str

    embedding_text: str
```

Cases may come from:

- curated golden synthetic dataset;
- validated public-dataset examples;
- manually accepted LangSmith failures/regressions;
- previous reviewed cases whose result was externally verified.

Do **not** automatically promote every model answer into the similarity corpus.

## What is embedded

Default embedding input should focus on problem semantics, not current numeric outcome:

```text
QUESTION:
{question}

COVENANT TYPE:
{metric_type}

RULE:
{normalized covenant text / metric semantics}
```

Do not embed:

- final numeric answer as the main semantic signal;
- borrower-specific transaction IDs;
- arbitrary model rationale alone.

The goal is to retrieve structurally similar covenant problems.

## Cosine similarity

Cosine is calculated in ordinary Python, not by the LLM.

```python
cosine(a, b) = dot(a, b) / (norm(a) * norm(b))
```

The retriever returns deterministic top-K ordered by:

1. cosine similarity descending;
2. case_id ascending as tie-breaker.

Defaults:

```yaml
review:
  similarity_top_k: 5
  minimum_similarity: 0.55
```

Cases below the minimum similarity are omitted.

If no cases pass the threshold, the system performs the second review with no similar examples and keeps the current result flagged for low confidence.

## Similarity provider boundary

Reuse the existing embedding abstraction where practical rather than coupling review code to one vendor.

```python
class ReviewEmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

Possible implementations:

- local multilingual E5;
- provider embedding API;
- deterministic fake embeddings for tests.

The review service must not require a vector database for MVP. The expected corpus is small enough to hold embeddings in memory / DuckDB / artifact cache and compute cosine directly.

## First review prompt

Input:

- question;
- current answer;
- deterministic rationale;
- normalized covenant rule;
- verifier issues;
- compiler confidence where known.

Task:

```text
Check whether the current answer is supported by the supplied deterministic evidence.
Do not recalculate from unstated data.
Do not invent a different numeric value.
Return a structured ReviewDecision and confidence from 0 to 1.
```

## Similarity-assisted second review

On fallback, append the top similar cases:

```text
CURRENT CASE
question: ...
answer: ...
rationale: ...

SIMILAR VALIDATED CASE 1
similarity: 0.91
question: ...
answer: ...
rationale: ...

SIMILAR VALIDATED CASE 2
...
```

Prompt instruction:

```text
Use similar cases only to check the reasoning pattern.
Their borrower IDs, thresholds, values and transaction IDs do not apply to the current case.
The current numeric result can only come from the current deterministic calculation.
```

## Review outcome semantics

### Accepted

```text
review.accepted = true
confidence >= threshold
no deterministic verification contradiction
```

The existing `CovenantResult` proceeds to report unchanged.

### Low confidence but valid deterministic result

If re-review remains below threshold while deterministic verifier is clean:

```text
keep deterministic result
mark review_status = low_confidence
```

Do not replace a verified calculation with model speculation.

### Deterministic verification issue

If verifier reports a contradiction:

```text
review cannot magically repair the number
```

The final result stays partial/failed or is routed to an existing bounded repair path.

### Reviewer contradiction

If model proposes an illegal number/evidence/verdict:

```text
reject reviewer output
keep deterministic result
review_status = invalid_reviewer_output
```

## ReviewedResult

Keep review metadata separate from the official submission model.

```python
class ReviewedResult(BaseModel):
    result: CovenantResult
    review: ReviewDecision
    review_status: Literal[
        "accepted",
        "accepted_after_similarity",
        "low_confidence",
        "invalid_reviewer_output",
        "review_failed",
    ]
```

The submission serializer still consumes the authoritative `CovenantResult` fields only.

Review metadata exists for debugging, selection and reporting.

## Report integration

Add review between batch verification and final submission serialization:

```text
BatchEvaluationReport.results
        |
        v
ReviewPipeline
        |
        v
ReviewedBatchReport
        |
        +--> inspection / LangSmith
        |
        v
SubmissionSerializer(authoritative results)
```

The CLI should expose a command similar to:

```bash
halyk-covenants review-results \
  --results results.json \
  --db data/duckdb/hackathon.duckdb \
  --at-date 2026-04-30 \
  --review-corpus data/review_cases.json
```

Exact CLI shape may follow existing command conventions during implementation.

## Persistence

Add review-specific tables or JSON records without changing `covenant_results` semantics.

Suggested:

```sql
review_cases
review_decisions
review_embeddings
```

Minimum persisted fields:

- review run ID;
- borrower/covenant ID;
- review confidence;
- accepted flag;
- fallback used;
- similar case IDs + cosine scores;
- final review status;
- reviewer model/prompt version;
- structured issues.

## LangSmith tracing

Trace business stages:

```text
review.case
  review.rationale
  review.first_pass
  review.similarity.embed
  review.similarity.search
  review.second_pass
  review.validate
```

Metadata:

- borrower_id;
- covenant_id;
- result status;
- first-pass confidence;
- fallback trigger reason;
- top-K scores;
- final review status;
- reviewer model/prompt version.

Do not log hidden reasoning.

## Testing

All logic except live model/provider calls must be testable offline.

### Unit

- cosine similarity numerical correctness;
- zero-vector handling;
- deterministic tie ordering;
- threshold filtering;
- fallback trigger combinations;
- `ReviewDecision` validation;
- reviewer cannot replace number;
- reviewer cannot inject foreign transaction evidence;
- verdict cannot contradict comparator;
- rationale builder deterministic output.

### Integration

Using fake reviewer + fake embeddings:

1. confidence `0.90` -> no embedding call;
2. confidence `0.60` -> embeddings + top-K + second review;
3. verifier issue -> fallback even when confidence is high;
4. second review accepted -> `accepted_after_similarity`;
5. second review still uncertain -> deterministic result retained + `low_confidence`;
6. reviewer proposes another borrower's number -> rejected;
7. reviewer proposes foreign TX ID -> rejected;
8. empty corpus -> no crash;
9. embedding provider failure -> deterministic result retained + `review_failed`;
10. reviewer provider failure -> deterministic result retained + `review_failed`.

### Regression

Extend synthetic benchmark with repeated semantic question types across borrowers using different values so tests explicitly prove cosine retrieval does not copy numeric answers.

## Provider limitations

Current Codex environment may not have live DeepSeek/embedding API access. Implementation should:

- keep reviewer and embedding provider injectable;
- cover orchestration with fakes;
- keep live-provider tests optional/skipped under CI when credentials are absent;
- never claim confidence quality is calibrated until evaluated against labeled review cases.

## Success criteria

The feature is complete when:

- review runs after deterministic verification and before final reporting;
- every reviewed case has question + answer + deterministic rationale;
- `confidence < 0.70` triggers similarity retrieval;
- deterministic verifier/compiler signals can trigger fallback independently of LLM confidence;
- embeddings + cosine top-K are implemented without an LLM deciding similarity;
- similar cases cannot change current numeric truth by copying another case;
- offline tests cover the full branch logic;
- LangSmith exposes first review, fallback retrieval and second review as distinct spans;
- official submission schema remains isolated from review metadata;
- no change is merged into `main` automatically.
