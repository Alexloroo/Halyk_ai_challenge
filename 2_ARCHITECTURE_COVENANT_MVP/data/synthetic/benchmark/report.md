# Synthetic Covenant Benchmark Report

## Validation Report

### Overall Assessment: Share with caveats

- **Dataset version:** `2026.08.02-v1`
- **Scope:** Golden CovenantSpec execution against synthetic XLSX transactions
- **Methodology:** Each borrower/covenant pair is evaluated independently through DuckDBStore and EvaluationService. Number, verdict, and evidence transaction are scored independently.

### Component Accuracy

| Metric | Result |
|---|---:|
| Cases | 10 |
| Component score | 29/30 |
| Component accuracy | 96.67% |
| Number accuracy | 100.00% |
| Verdict accuracy | 100.00% |
| Evidence accuracy | 90.00% |
| Full exact-match accuracy | 90.00% |

### Case Results

| Case | Number | Verdict | Evidence | Score | Status |
|---|---:|---|---|---:|---|
| ALPHA-SUM-APRIL | 16000000.000000 | violated | null | 3/3 | success |
| ALPHA-MAX-APRIL | 6000000.000000 | violated | A002 | 3/3 | success |
| ALPHA-COUNT-TRIGGER | 3 | violated | null | 2/3 | partial |
| ALPHA-MIN-INCOMING | 2000000.000000 | complied | null | 3/3 | success |
| BETA-AVG-APRIL | 4000000.000000 | complied | null | 3/3 | success |
| BETA-SUM-BOUNDARY | 12000000.000000 | complied | null | 3/3 | success |
| ALPHA-SUM-EMPTY | 0.000000 | complied | null | 3/3 | success |
| ALPHA-MAX-EMPTY | null | unknown | null | 3/3 | partial |
| GAMMA-SUM-DUPLICATE | 7000000.000000 | violated | null | 3/3 | success |
| BETA-MAX-ISOLATION | 6000000.000000 | complied | null | 3/3 | success |

### Data Quality Review

- **Grain:** one source row per workbook transaction row; 14 rows and 11 columns.
- **Date range:** 2026-04-01 to 2026-05-01.
- **Borrowers:** 000777, B001, B002.
- **Currencies:** KZT, USD.
- **Exact duplicate rows beyond first:** 1.

#### DQ-001 — Medium severity

- **Evidence:** 1 exact duplicate beyond the first occurrence (7.14% of 14 rows).
- **Risk:** Aggregate sums and counts include the retained duplicate by design.
- **Remediation:** Keep the row for this benchmark; require a source-semantic deduplication policy before removing duplicates in production.
- **Confidence:** high

#### DQ-002 — High severity

- **Evidence:** The workbook contains both KZT and USD transaction rows.
- **Risk:** Unfiltered cross-currency aggregation would produce an invalid monetary metric.
- **Remediation:** Require covenant currency filters or an explicit approved FX conversion rule.
- **Confidence:** high

#### DQ-003 — Low severity

- **Evidence:** Transaction rows are intentionally not ordered by transaction_date.
- **Risk:** Evidence selection is unreliable if code depends on source row order.
- **Remediation:** Always order trigger/evidence candidates by date and transaction ID.
- **Confidence:** high

### Required Caveats

- PDF extraction, OCR, borrower discovery, and covenant compilation are not scored.
- TRIGGER_TRANSACTION evidence selection is outside Phase 1–3 and is expected to miss one evidence component.
- The exact duplicate is intentionally retained and contributes to aggregate metrics.

### Calculation Spot-Checks

- Alpha April SUM independently reconciles to 5M + 6M + 5M = 16M KZT.
- Beta April AVG independently reconciles to (3M + 3M + 6M) / 3 = 4M KZT.
- Gamma duplicate case reconciles to 1M + 2M + 2M + duplicated 2M = 7M KZT.
- ALPHA-COUNT-TRIGGER earns number and verdict credit but misses evidence credit.
