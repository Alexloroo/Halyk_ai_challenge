"""Halyk AI Challenge — covenant compliance from dirty financial documents.

Reads the ledger and the document archive, decides COMPLIANT/BREACH for each of
the 36 cells in the submission template, and writes submission.json.

Layout, deliberately flat — one module per pipeline stage:

    paths       where the dataset lives
    ledger      CSV -> LedgerEntry, scenario derived from txn_id
    categorize  free-text description -> Category
    docs        archive -> Document, with type and edition
    clauses     current credit agreement -> clause text
    rules       clause text -> executable Rule
    evaluate    Rule + entries -> actual, status
    evidence    counterfactual search for the deciding transaction
    submit      fill the template
    score       measure against the answer key
    run         orchestration
"""

__all__ = ["__version__"]
__version__ = "1.0.0"
