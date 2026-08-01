# Dockerized Synthetic Covenant Benchmark Design

## Scope

This increment packages the existing Phase 1–3 covenant evaluator in Docker and adds a
reproducible synthetic benchmark. The benchmark evaluates manually curated, golden
`CovenantSpec` objects against generated transaction data. It does not claim to measure PDF
parsing, OCR, covenant discovery, borrower resolution from documents, or LLM compilation.

The generated PDFs are realistic source artifacts for future extraction work. Their deliberate
document defects are recorded in a manifest and visually verified, but they do not affect the
current evaluator score.

## Runtime architecture

A single multi-stage `Dockerfile` produces one runtime image. Docker Compose exposes two
one-shot services built from that image:

1. `generate-synthetic` creates all PDF, XLSX, golden-spec, Q&A, and manifest artifacts.
2. `benchmark` waits for successful generation, executes every benchmark case, and writes JSON
   and Markdown reports.

Both services mount `./data/synthetic` at `/app/data/synthetic`. The supported full command is:

```bash
docker compose up --build --abort-on-container-exit benchmark
```

The same image retains the existing `evaluate` CLI command. It runs as a non-root user and uses
DejaVu fonts for Cyrillic PDF content.

## Synthetic artifact set

The generator owns every file below so the dataset can be recreated deterministically:

```text
data/synthetic/
├── documents/
│   ├── alpha_trade_contract.pdf
│   └── borrower_limits_appendix.pdf
├── transactions/
│   └── synthetic_transactions.xlsx
├── covenants/
│   ├── COV-ALPHA-SUM.json
│   ├── COV-ALPHA-MAX.json
│   └── ...
├── benchmark/
│   ├── cases.json
│   ├── qa_pairs.jsonl
│   ├── qa_pairs.md
│   ├── report.json
│   └── report.md
└── manifest.json
```

### PDF 1: text contract

`alpha_trade_contract.pdf` contains native Russian text for Alpha Trade and a small summary
table. It deliberately includes realistic defects:

- borrower name variants (`ТОО «Альфа Трейд»`, `ALFA TRADE LLP`);
- a soft line break inside a threshold;
- a typo in one noncritical word;
- mixed comma/period numeric formatting;
- an amendment date separated from the rule it qualifies;
- a footer close to body text.

The substantive rules remain recoverable by a human and have unambiguous golden specs.

### PDF 2: tabular appendix

`borrower_limits_appendix.pdf` contains introductory text and a multi-borrower table. It
deliberately includes:

- wrapped and visually crowded cells;
- an abbreviated borrower name;
- a repeated table header on page two;
- a footnote exception below the table;
- a blank optional currency cell;
- one threshold expressed with a textual unit (`млн KZT`).

The table includes covenants for Alpha Trade, Beta Logistics, and borrower `000777` so borrower
isolation and mixed presentation styles can be exercised later.

## XLSX workbook

`synthetic_transactions.xlsx` contains four sheets:

- `transactions`: canonical transaction columns, with monetary cells stored as numeric values
  and all identifiers stored as text;
- `borrowers`: borrower IDs, canonical names, alternate names, and BIN-like synthetic IDs;
- `data_dictionary`: column definitions and expected types;
- `known_anomalies`: deliberate anomalies and the behavior expected from ingestion.

Deliberate transaction-data edge cases include leading-zero IDs, an exact duplicate retained for
auditability, optional nulls, out-of-order dates, an exact comparator boundary, a different
currency row, and transactions belonging to another borrower.

No real person, account, BIN/IIN, or company data is used.

## Golden covenants and Q&A

Each covenant is stored as one strict JSON `CovenantSpec`. The Q&A set is derived from an
explicit benchmark case registry rather than inferred from PDF text. Every case includes:

- case ID and natural-language Russian question;
- PDF/document reference and covenant ID;
- borrower ID and evaluation date;
- expected verdict;
- exact expected number as a decimal string or integer;
- expected evidence transaction ID or null;
- expected internal status and a short human explanation.

JSONL is the machine-readable form. Markdown is the reviewer-friendly form.

## Benchmark coverage

The case set covers:

- SUM, COUNT, MAX, MIN, and AVG;
- all practical comparator directions and an exact `<=` boundary;
- borrower isolation;
- transaction filters and calendar-month windows;
- zero semantics for empty SUM and COUNT sets;
- unknown/partial semantics for empty MAX/MIN/AVG sets;
- MAX evidence selection;
- retained duplicate rows;
- a COUNT trigger-evidence case that exposes the currently unsupported trigger selector.

The trigger-evidence case is intentionally expected to receive number and verdict credit while
losing evidence credit. A benchmark below 100% is acceptable when it documents a real Phase 1–3
capability gap.

## Scoring

For every benchmark case, compare expected and actual components independently:

```text
number_score   = 1 when exact normalized numeric values match, else 0
verdict_score  = 1 when labels match, else 0
evidence_score = 1 when transaction IDs or nulls match, else 0
component_score = number_score + verdict_score + evidence_score
full_exact_match = all three component scores equal 1
```

Aggregate report fields include total cases, total earned components, maximum components,
component accuracy, per-component accuracy, full exact-match accuracy, status counts, failed
case IDs, and the known limitations disclosed by the dataset manifest.

Decimal comparison never passes through `float`; values are parsed as `Decimal`. Count values
remain integers.

## Validation and quality gates

Generation validation checks:

- both PDFs open and contain at least one page;
- expected native text markers exist in both PDFs;
- the workbook has the four required sheets and canonical transaction headers;
- every case references an existing covenant, borrower, document, and transaction workbook;
- every golden covenant validates as `CovenantSpec`;
- every Q&A record is derivable from a benchmark case without hidden values;
- all output hashes are recorded in `manifest.json`.

Benchmark validation checks:

- result count equals case count;
- component totals recompute exactly from case details;
- no unreported exception terminates the run;
- reports are deterministic across two consecutive runs;
- the host and Docker results match.

PDF verification includes rendering representative pages to images and visually inspecting text,
tables, Cyrillic fonts, line wrapping, and footnotes.

## Error behavior

Synthetic generation writes to a temporary directory and only replaces the target artifact set
after all files validate. A failed generation leaves the last valid dataset intact.

Benchmark cases execute independently. A malformed covenant or evaluator exception becomes a
failed case record and does not stop remaining cases. The command exits nonzero only for an
invalid dataset, an unexpected runner failure, or a score below an explicitly supplied minimum.

## Documentation

The README documents local and Docker workflows, artifact meanings, benchmark metrics, the
intentional defects, and the explicit limitation that PDF extraction is not scored in this
increment.
