from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.logging import configure_logging
from halyk_covenants.storage import DuckDBStore

app = typer.Typer(
    name="halyk-covenants",
    help="Load structured transactions and evaluate deterministic covenant specifications.",
    no_args_is_help=True,
)


@app.callback()
def main(
    log_level: Annotated[str, typer.Option("--log-level")] = "WARNING",
) -> None:
    """Halyk covenant evaluation MVP."""
    configure_logging(log_level)


@app.command("evaluate")
def evaluate_command(
    transactions: Annotated[
        Path,
        typer.Option(
            "--transactions",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ],
    covenant: Annotated[
        Path,
        typer.Option(
            "--covenant",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ],
    borrower_id: Annotated[str, typer.Option("--borrower-id")],
    at_date: Annotated[datetime | None, typer.Option("--at-date", formats=["%Y-%m-%d"])] = None,
    db_path: Annotated[str, typer.Option("--db")] = ":memory:",
) -> None:
    """Load one transaction file and evaluate one borrower/covenant pair."""
    try:
        spec = CovenantSpec.model_validate_json(covenant.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        typer.echo(f"Invalid covenant specification: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if borrower_id not in spec.borrower_ids:
        typer.echo(
            f"Borrower {borrower_id!r} is not assigned to covenant {spec.covenant_id!r}",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        with DuckDBStore(db_path) as store:
            store.load_transactions(transactions)
            evaluation_date = at_date.date() if at_date is not None else None
            result = EvaluationService(store).evaluate(spec, borrower_id, evaluation_date)
    except (OSError, ValidationError, ValueError) as exc:
        typer.echo(f"Transaction ingestion failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(result.model_dump_json(indent=2))


if __name__ == "__main__":
    app()
