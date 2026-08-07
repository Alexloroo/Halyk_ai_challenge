# Merge accuracy and leakage audit

Date: 2026-08-07

## Result

The merged pipeline scores **35.0 / 36.0 (97.2222%)** with a fresh LLM run:

- status matches: 35 / 36;
- exact actual values: 36 / 36;
- evidence matches: 35 / 36;
- exact cells: 35 / 36.

The `data/check/submission.json` stored on `covenant-architecture-v3` also scores
35.0 / 36.0 with the repository scorer. The `35.30` claim is not reproducible
with that scorer.

## Leakage findings

### Runtime target leakage: not found

`solve()` does not read `ground_truth.json`. The CLI builds and writes the
submission first, and only then lets fulltrace compare the immutable submission
with ground truth. A regression test runs the same input first without ground
truth and then with deliberately poisoned ground truth; both submissions are
identical.

The LLM receives the extracted covenant clause only. It is not passed the
submission, ground truth, score report, scenario answer, or expected transaction.

### Development-set leakage / benchmark overfitting: present in the source branch

The source branch contains a score-diagnostic report with exact expected values
and a list of cell-specific fixes. Its accuracy work was therefore developed
against the evaluation labels, so 97.22% is a tuned in-sample score and is not an
unbiased estimate of performance on new cases.

The branch also contained `scanned.py`, with manual transcriptions selected by
three exact PDF hash filenames. That is dataset memorization rather than a
general OCR path. The merged result excludes it and uses the Dockerized
Tesseract fallback from `main`.

The branch's committed formula cache was keyed by exact clause text. It did not
contain ground-truth answers, but it bypassed a clean model invocation on this
fixed benchmark. The merged result excludes the cache and the reported score
comes from fresh DeepSeek calls.

## Remaining mismatch

Only `P4 / 6.3` differs. The unrounded calculation is:

```text
288,417.52 / 7,004,318.47 = 0.0411770997
threshold = 0.04
```

The agreement says the ratio must not exceed `0.04`, so the implementation
returns `BREACH`. Ground truth returns `COMPLIANT` while also storing the rounded
actual as `0.04`. Making this cell match would require adopting an unstated
"round before verdict" convention inferred from this one label. That change was
not made because it would be target-driven and can produce incorrect boundary
decisions on new data.

## Conclusion

The merged runtime has no direct target leakage, and its calculations are fully
traceable. The observed score is nevertheless not a clean generalization metric:
the feature branch was explicitly tuned using this dataset's ground truth. Treat
35.0 / 36.0 as an in-sample benchmark result, not as expected out-of-sample
accuracy.
