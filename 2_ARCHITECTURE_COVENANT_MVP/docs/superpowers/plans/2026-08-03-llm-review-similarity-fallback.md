# LLM Review + Similarity Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a final LLM review layer before reporting that accepts high-confidence deterministic answers and performs embedding/cosine-assisted re-review for low-confidence or verifier-flagged cases without allowing semantic similarity to replace deterministic numeric truth.

**Architecture:** Introduce a focused `review/` package with strict domain models, deterministic rationale building, cosine retrieval over curated validated cases, reviewer orchestration, and output validation. Integrate it after `BatchEvaluationPipeline` and before submission serialization; keep `CovenantResult` authoritative and store review metadata separately. Live LLM/embedding providers remain injectable and optional in CI.

**Tech Stack:** Python 3.12, Pydantic, NumPy, existing LangChain model abstraction, existing LangSmith tracing helpers, DuckDB, pytest, Ruff.

## Global Constraints

- Work only on branch `codex-2`.
- Do not merge into `codex-1` or `main`.
- Default review confidence threshold is exactly `0.70`.
- Similarity fallback must trigger on low reviewer confidence, deterministic verification issues, non-success result status, or low compiler confidence.
- Cosine similarity is calculated in ordinary Python, not by an LLM.
- Similar cases may guide reasoning patterns but must never replace the current deterministic number with another case's value.
- Evidence transaction IDs must remain current-case verified evidence only.
- Submission serialization continues to consume authoritative `CovenantResult` fields only.
- Live DeepSeek/embedding-provider calls are optional and skipped when credentials/runtime are unavailable.

---

### Task 1: Review domain models and deterministic rationale

**Files:**
- Create: `src/halyk_covenants/review/models.py`
- Create: `src/halyk_covenants/review/rationale.py`
- Create: `src/halyk_covenants/review/__init__.py`
- Test: `tests/unit/test_review_models.py`
- Test: `tests/unit/test_review_rationale.py`

**Interfaces:**
- Consumes: `CovenantResult`, `CovenantSpec`, `Calculation`.
- Produces: `ReviewCase`, `ReviewDecision`, `ReviewedResult`, `SimilarReviewCase`, `SimilarityMatch`, `build_rationale(...) -> str`.

- [ ] **Step 1: Write failing model-validation tests**

```python
from decimal import Decimal
import pytest
from pydantic import ValidationError
from halyk_covenants.review import ReviewDecision


def test_review_confidence_is_bounded():
    with pytest.raises(ValidationError):
        ReviewDecision(
            accepted=True,
            confidence=Decimal("1.01"),
            verdict="complied",
            number=1,
            rationale="ok",
        )
```

- [ ] **Step 2: Run the new model tests and confirm they fail because the package/types do not exist**

Run: `pytest -q tests/unit/test_review_models.py`
Expected: collection/import failure for `halyk_covenants.review`.

- [ ] **Step 3: Implement strict Pydantic review models**

```python
class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: bool
    confidence: Decimal = Field(ge=0, le=1)
    verdict: Literal["complied", "violated", "unknown"]
    number: Decimal | int | None = None
    evidence_transaction_id: str | None = None
    rationale: str
    issues: list[str] = Field(default_factory=list)
    used_similarity_fallback: bool = False
    similar_case_ids: list[str] = Field(default_factory=list)
```

Add `ReviewCase`, `ReviewedResult`, `SimilarReviewCase`, and `SimilarityMatch` exactly as defined by the design spec, with `review_status` constrained to `accepted`, `accepted_after_similarity`, `low_confidence`, `invalid_reviewer_output`, `review_failed`.

- [ ] **Step 4: Write failing rationale test**

```python
def test_rationale_contains_calculation_and_comparison():
    text = build_rationale(case_fixture)
    assert "16000000" in text
    assert "15000000" in text
    assert "violated" in text
    assert "3 matched transactions" in text
```

- [ ] **Step 5: Implement deterministic rationale builder**

Build rationale only from covenant rule + `Calculation` + `CovenantResult`: metric, row count, value/unit, comparator, threshold, period/effective dates, evidence ID. Never call a model in `rationale.py`.

- [ ] **Step 6: Run focused tests**

Run: `pytest -q tests/unit/test_review_models.py tests/unit/test_review_rationale.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/halyk_covenants/review tests/unit/test_review_models.py tests/unit/test_review_rationale.py
git commit -m "feat: add review domain and deterministic rationale"
```

---

### Task 2: Cosine similarity retriever with curated cases

**Files:**
- Create: `src/halyk_covenants/review/similarity.py`
- Test: `tests/unit/test_review_similarity.py`

**Interfaces:**
- Consumes: `ReviewEmbeddingProvider.embed(texts: list[str]) -> list[list[float]]`, `SimilarReviewCase`.
- Produces: `cosine_similarity(a, b) -> float`, `SimilarityRetriever.search(query_case, k, minimum_similarity) -> list[SimilarityMatch]`.

- [ ] **Step 1: Write failing cosine/retrieval tests**

```python
def test_cosine_similarity_handles_zero_vector():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_similarity_ties_are_ordered_by_case_id():
    matches = retriever.search(query, k=2, minimum_similarity=0)
    assert [item.case.case_id for item in matches] == ["A", "B"]
```

Also cover threshold filtering and `top_k`.

- [ ] **Step 2: Run test and confirm failure**

Run: `pytest -q tests/unit/test_review_similarity.py`
Expected: missing implementation.

- [ ] **Step 3: Implement cosine calculation and retriever**

```python
def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError("embedding dimensions must match")
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return 0.0 if denom == 0 else float(np.dot(a, b) / denom)
```

`SimilarityRetriever` embeds the query embedding text once, embeds/caches corpus values, sorts by `(-similarity, case_id)`, and omits values below `minimum_similarity`.

- [ ] **Step 4: Run focused test**

Run: `pytest -q tests/unit/test_review_similarity.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/halyk_covenants/review/similarity.py tests/unit/test_review_similarity.py
git commit -m "feat: add cosine review retriever"
```

---

### Task 3: Reviewer service, fallback triggers, and authority validation

**Files:**
- Create: `src/halyk_covenants/review/reviewer.py`
- Create: `src/halyk_covenants/review/service.py`
- Test: `tests/unit/test_review_service.py`

**Interfaces:**
- Consumes: `Reviewer.review(case, similar_cases=...) -> ReviewDecision`, `SimilarityRetriever`, `ReviewCase`.
- Produces: `ReviewService.review(case) -> ReviewedResult`.

- [ ] **Step 1: Write failing orchestration tests with fakes**

Cover exactly:

```python
def test_high_confidence_skips_similarity(): ...
def test_low_confidence_calls_similarity_and_reviews_again(): ...
def test_verifier_issue_forces_fallback_even_with_high_confidence(): ...
def test_non_success_result_forces_fallback(): ...
def test_low_compiler_confidence_forces_fallback(): ...
def test_reviewer_cannot_replace_deterministic_number(): ...
def test_reviewer_cannot_inject_foreign_evidence(): ...
def test_reviewer_verdict_cannot_contradict_comparator(): ...
def test_empty_corpus_does_not_crash(): ...
def test_embedding_failure_keeps_deterministic_result_and_marks_review_failed(): ...
def test_reviewer_failure_keeps_deterministic_result_and_marks_review_failed(): ...
```

- [ ] **Step 2: Run focused test and verify failures**

Run: `pytest -q tests/unit/test_review_service.py`
Expected: missing service/types.

- [ ] **Step 3: Implement authority validator**

Validation rules before accepting reviewer output:

```python
if decision.number != case.answer.number:
    raise InvalidReviewerDecision("reviewer changed deterministic number")
if decision.evidence_transaction_id not in {None, case.answer.evidence_transaction_id}:
    raise InvalidReviewerDecision("reviewer injected foreign evidence")
if case.answer.number is not None and covenant.condition.threshold is not None:
    expected = compare(case.answer.number, comparator, threshold)
    expected_verdict = "complied" if expected else "violated"
    if decision.verdict != expected_verdict:
        raise InvalidReviewerDecision("reviewer verdict contradicts deterministic comparator")
```

- [ ] **Step 4: Implement fallback trigger**

```python
needs_fallback = (
    first.confidence < confidence_threshold
    or bool(case.verification_issues)
    or case.answer.status != "success"
    or (
        case.compiler_confidence is not None
        and case.compiler_confidence < compiler_confidence_threshold
    )
)
```

- [ ] **Step 5: Implement two-pass review orchestration**

First pass has no similar cases. On fallback, retrieve top-K and call the same reviewer again with `similar_cases`. If second review is valid and confidence >= threshold, status is `accepted_after_similarity`; otherwise retain current deterministic result and mark `low_confidence`. Provider failures yield `review_failed`. Illegal reviewer outputs yield `invalid_reviewer_output`.

- [ ] **Step 6: Add business tracing**

Trace stages exactly:

```text
review.case
review.first_pass
review.similarity.embed
review.similarity.search
review.second_pass
review.validate
```

Metadata includes borrower/covenant IDs, first confidence, fallback reasons, similarity scores/IDs, final status, reviewer model/prompt version when available.

- [ ] **Step 7: Run focused tests**

Run: `pytest -q tests/unit/test_review_service.py`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/halyk_covenants/review tests/unit/test_review_service.py
git commit -m "feat: add guarded two-pass review service"
```

---

### Task 4: LangChain reviewer adapter, persistence, and batch review pipeline

**Files:**
- Create: `src/halyk_covenants/review/langchain_reviewer.py`
- Create: `src/halyk_covenants/pipeline/review.py`
- Modify: `src/halyk_covenants/storage/duckdb_store.py`
- Modify: `src/halyk_covenants/pipeline/__init__.py`
- Test: `tests/integration/test_review_pipeline.py`

**Interfaces:**
- Consumes: existing LangChain chat model, `BatchEvaluationReport`, covenant/calculation rows in DuckDB.
- Produces: `ReviewedBatchReport`, persisted `review_decisions` records.

- [ ] **Step 1: Write failing integration test**

Use fake reviewer and fake embeddings, persist one compiled covenant/calculation/result, run `ReviewPipeline`, assert:

```python
assert report.reviewed_results[0].review_status == "accepted"
row = store.connection.execute("SELECT status FROM review_decisions").fetchone()
assert row[0] == "accepted"
```

- [ ] **Step 2: Run integration test and verify failure**

Run: `pytest -q tests/integration/test_review_pipeline.py`
Expected: missing pipeline/table.

- [ ] **Step 3: Extend DuckDB schema**

Add focused tables:

```sql
CREATE TABLE IF NOT EXISTS review_decisions (
    review_run_id VARCHAR NOT NULL,
    borrower_id VARCHAR NOT NULL,
    covenant_id VARCHAR NOT NULL,
    evaluation_date DATE NOT NULL,
    status VARCHAR NOT NULL,
    decision_json JSON NOT NULL,
    PRIMARY KEY (review_run_id, borrower_id, covenant_id)
);
```

Keep curated corpus as file/input objects for MVP; do not add a vector DB.

- [ ] **Step 4: Implement `ReviewPipeline`**

For every `CovenantResult`:

1. load matching `CovenantSpec`/active group;
2. load calculation JSON by `calculation_id` when available;
3. derive verifier issues from batch verification + pair scope;
4. build deterministic question if explicit question is absent: `Evaluate covenant {id} for borrower {id} as of {date}.`;
5. build `ReviewCase` + rationale;
6. call `ReviewService`;
7. persist review metadata;
8. return `ReviewedBatchReport` while leaving `result` unchanged.

- [ ] **Step 5: Implement LangChain structured reviewer adapter**

Use model structured output for `ReviewDecision`. Prompt explicitly says similar examples are patterns only and current numeric truth comes from current deterministic evidence. Do not ask for hidden reasoning; request a concise evidence rationale.

- [ ] **Step 6: Run integration tests**

Run: `pytest -q tests/integration/test_review_pipeline.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/halyk_covenants/review src/halyk_covenants/pipeline src/halyk_covenants/storage tests/integration/test_review_pipeline.py
git commit -m "feat: integrate review pipeline and persistence"
```

---

### Task 5: CLI/report integration and regression cases

**Files:**
- Modify: `src/halyk_covenants/cli.py`
- Create: `tests/integration/test_review_cli.py`
- Create: `tests/integration/test_review_regression.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `review-results` CLI command and a reviewed JSON report suitable for inspection/LangSmith before submission serialization.

- [ ] **Step 1: Write failing CLI test**

Test a fake/offline path that loads evaluation results, review corpus, and deterministic fake reviewer/embedding providers through a lower-level command helper; do not require live DeepSeek in CI.

- [ ] **Step 2: Add `review-results` command**

Required arguments:

```text
--results
--db
--at-date
--review-corpus
--output
```

Optional configuration:

```text
--confidence-threshold (default 0.70)
--top-k (default 5)
--minimum-similarity (default 0.55)
```

Live command may reuse configured DeepSeek model and a local multilingual embedding provider, while tests inject fakes through the pipeline/service API.

- [ ] **Step 3: Add regression test proving no answer copying**

Corpus:

```text
Case A question: monthly outgoing limit for borrower A
number: 16000000

Current case: monthly outgoing limit for borrower B
number: 8000000
```

Make embeddings intentionally highly similar and reviewer initially low-confidence. Assert final authoritative number remains `8000000` and foreign evidence is never accepted.

- [ ] **Step 4: Document operational flow**

README sequence:

```bash
halyk-covenants preprocess ...
halyk-covenants evaluate-all ... --output results.json
halyk-covenants review-results ... --results results.json --output reviewed.json
halyk-covenants serialize-submission --results results.json ...
```

Explicitly state that the submission serializer still consumes deterministic `results.json`; `reviewed.json` is a quality gate/inspection artifact unless a later bounded repair step changes deterministic state.

- [ ] **Step 5: Run focused and full tests**

Run:

```bash
pytest -q tests/unit/test_review_models.py tests/unit/test_review_rationale.py tests/unit/test_review_similarity.py tests/unit/test_review_service.py tests/integration/test_review_pipeline.py tests/integration/test_review_cli.py tests/integration/test_review_regression.py
ruff check src tests
pytest -q
```

Expected: all offline tests pass; existing live DeepSeek/Paddle/LangSmith tests remain intentionally skipped when unavailable.

- [ ] **Step 6: Commit**

```bash
git add src/halyk_covenants/cli.py tests README.md
git commit -m "feat: expose reviewed report workflow"
```

---

## Final verification checklist

- [ ] Branch is `codex-2` and is based on the approved `codex-1` head.
- [ ] No reviewer path mutates authoritative deterministic `number` from similarity examples.
- [ ] `confidence < 0.70` triggers similarity fallback.
- [ ] Deterministic verifier/status/compiler signals independently trigger fallback.
- [ ] Cosine calculation and ordering are deterministic.
- [ ] Empty corpus/provider failure keeps deterministic result and produces explicit review status.
- [ ] LangSmith spans make first-pass, retrieval, and second-pass visible separately.
- [ ] Review metadata is persisted separately from `covenant_results`.
- [ ] Submission serializer contract remains unchanged.
- [ ] Ruff passes.
- [ ] Full pytest passes except intentional live-provider skips.
- [ ] Nothing is merged into `codex-1` or `main`.
