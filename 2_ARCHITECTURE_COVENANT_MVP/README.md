# Halyk Covenant Evaluation MVP

This repository implements Phases 1–3 of `2_ARCHITECTURE_COVENANT_MVP.md`:

- strict Pydantic domain models;
- CSV, Excel, and Parquet transaction ingestion into DuckDB;
- deterministic SUM, COUNT, MAX, MIN, and AVG covenant evaluation;
- exact comparator semantics and MAX evidence selection;
- partial and failed results that do not discard valid answer components;
- a minimal end-to-end CLI;
- a Dockerized synthetic-data generator and component-level benchmark.

PDF parsing, OCR, covenant discovery, LLM compilation, vector retrieval, and the official
submission serializer are deliberately outside this milestone.

## Setup

Python 3.12 or newer is required.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Canonical transaction input

CSV, `.xlsx`, and Parquet are supported. Required source columns are:

| Canonical field | Required | DuckDB type |
|---|---:|---|
| `transaction_id` | yes | `VARCHAR` |
| `date` or `transaction_date` | yes | `DATE` |
| `amount` | yes | `DECIMAL(38, 6)` |
| `borrower_id` | no | `VARCHAR` |
| `account_id` | no | `VARCHAR` |
| `currency` | no | `VARCHAR` |
| `direction` | no | `VARCHAR` |
| `counterparty_id` | no | `VARCHAR` |
| `counterparty_name` | no | `VARCHAR` |
| `purpose` | no | `VARCHAR` |
| `source_row_id` | no | `VARCHAR` |

Noncanonical headers can be adapted through `DuckDBStore.load_transactions(...,
column_mapping={canonical: source})`. Every row is also stored in `raw_transactions` as a JSON
payload with source path, row number, and SHA-256 hash. Exact duplicates are detectable but are
not silently removed.

## CLI

Evaluate a strict `CovenantSpec` JSON file against a transaction file:

```bash
.venv/bin/halyk-covenants evaluate \
  --transactions tests/fixtures/transactions.csv \
  --covenant covenant.json \
  --borrower-id 000341 \
  --at-date 2026-04-30
```

The result is the internal `CovenantResult` JSON. Decimal values are serialized as JSON strings
to avoid precision loss. Presentation rules for the future official submission belong in a
separate serializer and are not embedded in the evaluator.

Generate the reproducible synthetic dataset:

```bash
.venv/bin/halyk-covenants generate-synthetic --output data/synthetic
```

Run the deterministic evaluator benchmark against its golden `CovenantSpec` fixtures:

```bash
.venv/bin/halyk-covenants benchmark --dataset data/synthetic
```

The command exits non-zero when generation/validation fails. An optional acceptance threshold is
available through `--min-component-accuracy`, for example `0.95`.

## Docker

Docker Compose uses one multi-stage Python 3.12 image and two ordered one-shot services. The
application runs as the non-root `appuser`; `data/` is the only writable bind mount.

```bash
docker compose build
docker compose run --rm generate-synthetic
docker compose run --rm benchmark
```

The `benchmark` service also declares a dependency on the generator, so this single command runs
the complete workflow:

```bash
docker compose run --rm benchmark
```

Generated files remain on the host under `data/synthetic/`.

## Synthetic benchmark artifacts

The checked-in dataset is deliberately small, inspectable, and deterministic:

- `documents/alpha_trade_contract.pdf` — native Russian text plus a summary table;
- `documents/borrower_limits_appendix.pdf` — a two-page borrower-limit table;
- `transactions/synthetic_transactions.xlsx` — transactions, borrowers, data dictionary, and
  known-anomalies sheets;
- `covenants/*.json` — golden executable covenant rules;
- `benchmark/qa_pairs.md` and `.jsonl` — ten question/answer fixtures;
- `benchmark/report.md` and `.json` — component scoring and data-quality findings;
- `manifest.json` — SHA-256 and byte size for every immutable input artifact.

The PDFs intentionally include realistic extraction traps: mixed Cyrillic/Latin borrower names,
wrapped thresholds, a harmless typo, different number separators, a repeated table header,
abbreviated borrower names, a blank currency cell, and a footnote exception. The XLSX includes
out-of-order rows, a retained exact duplicate, a leading-zero borrower ID, a null optional field,
and mixed KZT/USD currencies.

This benchmark starts from the golden `CovenantSpec` files. It tests the implemented deterministic
execution layer, not future PDF/OCR extraction or LLM covenant compilation. The expected baseline
is 29/30 answer components (96.67%): number 100%, verdict 100%, evidence 90%, and full exact match
90%. The single expected miss is COUNT trigger evidence, which belongs to the later evidence
selector phase.

## Tests and quality checks

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```
