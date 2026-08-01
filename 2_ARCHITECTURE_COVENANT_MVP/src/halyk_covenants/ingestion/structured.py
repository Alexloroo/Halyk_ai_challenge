from pathlib import Path

import pandas as pd


def read_structured_file(path: Path) -> pd.DataFrame:
    """Read a supported structured file without coercing textual identifiers to numbers."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if suffix in {".xlsx", ".xlsm"}:
        return pd.read_excel(path, dtype=str, keep_default_na=False)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"Unsupported structured file format: {suffix or '<none>'}")
