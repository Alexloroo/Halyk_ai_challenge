# Synthetic trace diagnostic — source notes

## Scope and metric

- Controlling metric source: `trace/14_ground_truth/comparison.json`.
- Population: 102 covenant cells in the latest fulltrace; 66 synthetic `X1–X22`
  cells and 36 legacy `P*`/`B*` cells.
- Cell score: the unweighted local scorer's `cell_score`, maximum 1 per cell.
- Synthetic score: `sum(cell_score) / 66`.
- Exact-cell rate: count of `exact_cell_match=true` divided by cell count.

## Reproduction

```python
import json

rows = json.load(open("trace/14_ground_truth/comparison.json"))["comparisons"]
synthetic = [row for row in rows if row["scenario_id"].startswith("X")]
legacy = [row for row in rows if not row["scenario_id"].startswith("X")]

for name, sample in (("synthetic", synthetic), ("legacy", legacy)):
    score = sum(row["cell_score"] for row in sample)
    exact = sum(row["exact_cell_match"] for row in sample)
    print(name, score, score / len(sample), exact / len(sample))
```

## Primary loss attribution

The buckets below are mutually exclusive primary attributions of the current
22.354545-point synthetic loss. Some cells have interacting defects; notably
`X22/6.1` has both an audit parser error and an empty category set, and is assigned
to the audit bucket so losses are not double-counted.

| Primary cause | Cells | Current score loss | Trace evidence |
|---|---|---:|---|
| Category vocabulary/taxonomy | X6/6.3, X7/6.1, X10/6.1, X11/6.1, X11/6.3, X14/6.2, X15/6.3, X18/6.3, X19/6.3 | 7.500000 | `trace/10_rules`, `trace/03_ledger_categorized`, `trace/12_evaluation` |
| KYC substring entity matching | X6/6.1–6.2, X18/6.1–6.2, X19/6.1–6.2, X22/6.2 | 7.000000 | `trace/09_related_parties`, `trace/12_evaluation` |
| Wrong agreement selected by length | X12/6.1–6.3 | 3.000000 | `trace/05_documents_classified/documents.json`, `trace/07_documents_selected/X12.json`, `trace/10_rules/X12.json` |
| Hard-coded EBITDA definition | X16/6.1–6.3 | 2.000000 | `trace/12_evaluation/X16` |
| Cyrillic multiplier not parsed | X21/6.3 | 1.000000 | `trace/10_rules/X21.json`, `trace/12_evaluation/X21/6_3.json` |
| Greedy audit regex / wrong txn target | X22/6.1, X22/6.3 | 0.954545 | `trace/08_audit_and_fx/X22`, `trace/12_evaluation/X22` |
| Conditional calculation semantics | X13/6.1 | 0.500000 | `trace/12_evaluation/X13/6_1.json` |
| Evidence lineage/eligibility | X3/6.3, X9/6.1 | 0.400000 | `trace/08_audit_and_fx/X9`, `trace/12_evaluation/X3`, `trace/12_evaluation/X9` |

## Masked output defects

`X7/6.3` and `X20/6.1` emit a non-null evidence transaction while ground truth
expects null. The local scorer still awards 1.0 to both cells, so the 66.13%
synthetic score is less strict than the 54.55% exact-cell rate.

## Chart map

- Section: root-cause concentration.
- Question: which architecture defects account for the current synthetic score loss?
- Family/type: comparison / sorted bar.
- Dataset: `driver_loss`.
- Fields: `cause`, `score_loss`; retained context includes affected cell count,
  share of loss, first failing stage, confidence, and cell identifiers.
- Claim: category semantics, KYC matching, and document selection explain 17.5
  of 22.354545 lost points (78.28%).
- Palette: single-root preferred; no legend because this is one series.

