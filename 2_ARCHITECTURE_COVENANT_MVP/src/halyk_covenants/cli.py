import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from pydantic import TypeAdapter, ValidationError

from halyk_covenants.benchmark.reporting import write_benchmark_reports
from halyk_covenants.benchmark.runner import run_benchmark
from halyk_covenants.config import load_settings
from halyk_covenants.covenants import (
    CompilerGraph,
    CovenantCompiler,
    CovenantRegistry,
    LangChainCompilerRepairer,
)
from halyk_covenants.domain import CovenantResult, CovenantSpec
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.ingestion import PageQualityRouter, PDFIngestor
from halyk_covenants.llm import DeepSeekChatFactory, DeepSeekConfigurationError
from halyk_covenants.logging import configure_logging
from halyk_covenants.ocr import PaddleOCRProvider
from halyk_covenants.pipeline import BatchEvaluationPipeline, PreprocessPipeline
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.submission import (
    SubmissionSerializer,
    SubmissionValidator,
    load_submission_profile,
)
from halyk_covenants.synthetic.generator import generate_synthetic_dataset
from halyk_covenants.synthetic.validation import DatasetValidationError
from halyk_covenants.vlm import PaddleLayoutProvider

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


@app.command("generate-synthetic")
def generate_synthetic_command(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = Path("data/synthetic"),
) -> None:
    """Generate deterministic PDF, XLSX, golden covenant, and Q&A fixtures."""
    try:
        manifest = generate_synthetic_dataset(output)
    except (DatasetValidationError, OSError, ValueError) as exc:
        typer.echo(f"Synthetic dataset generation failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(manifest.model_dump_json(indent=2))


@app.command("benchmark")
def benchmark_command(
    dataset: Annotated[
        Path,
        typer.Option(
            "--dataset",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
        ),
    ] = Path("data/synthetic"),
    min_component_accuracy: Annotated[
        float,
        typer.Option("--min-component-accuracy", min=0.0, max=1.0),
    ] = 0.0,
) -> None:
    """Run the current deterministic evaluator against golden synthetic cases."""
    try:
        report = run_benchmark(dataset)
        json_path, markdown_path = write_benchmark_reports(report, dataset / "benchmark")
    except (DatasetValidationError, OSError, ValueError) as exc:
        typer.echo(f"Synthetic benchmark failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    payload = {
        "dataset_version": report.dataset_version,
        "summary": report.summary.model_dump(mode="json"),
        "report_json": str(json_path.resolve()),
        "report_markdown": str(markdown_path.resolve()),
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    required = Decimal(str(min_component_accuracy))
    if report.summary.component_accuracy < required:
        typer.echo(
            f"Component accuracy {report.summary.component_accuracy} "
            f"is below required minimum {required}",
            err=True,
        )
        raise typer.Exit(code=3)


@app.command("preprocess")
def preprocess_command(
    input_root: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    db_path: Annotated[Path, typer.Option("--db")] = Path("data/duckdb/hackathon.duckdb"),
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    enable_ocr: Annotated[bool, typer.Option("--ocr/--no-ocr")] = False,
) -> None:
    """Ingest structured files and compile PDF covenants through DeepSeek/LangGraph."""
    try:
        settings = load_settings(config_path)
        compiler_graph = None
        router = PageQualityRouter(native_text_min_chars=settings.ocr.native_text_min_chars)
        pdf_ingestor = PDFIngestor(
            router=router,
            max_page_pixels=settings.ocr.max_page_pixels,
        )
        if any(path.suffix.casefold() == ".pdf" for path in input_root.rglob("*")):
            if enable_ocr:
                ocr_provider = PaddleOCRProvider(
                    preferred_device=settings.ocr.device,
                    cpu_fallback=settings.ocr.cpu_fallback,
                )
                ocr_provider.validate_runtime()
                layout_provider = PaddleLayoutProvider(preferred_device=settings.ocr.device)
                pdf_ingestor = PDFIngestor(
                    router=router,
                    ocr=ocr_provider,
                    visual=layout_provider,
                    max_page_pixels=settings.ocr.max_page_pixels,
                )
            model = DeepSeekChatFactory(settings.deepseek).create()
            compiler_graph = CompilerGraph(
                compiler=CovenantCompiler(model),
                repairer=LangChainCompilerRepairer(model),
            )
        with DuckDBStore(db_path) as store:
            report = PreprocessPipeline(
                store,
                pdf_ingestor=pdf_ingestor,
                compiler_graph=compiler_graph,
                progress=lambda message: typer.echo(message, err=True),
            ).run(input_root)
    except (OSError, RuntimeError, ValueError, ValidationError, DeepSeekConfigurationError) as exc:
        typer.echo(f"Preprocessing failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(report.model_dump_json(indent=2))


@app.command("inspect-covenants")
def inspect_covenants_command(
    db_path: Annotated[Path, typer.Option("--db")] = Path("data/duckdb/hackathon.duckdb"),
) -> None:
    """Print strict compiled covenant specifications from the registry."""
    with DuckDBStore(db_path) as store:
        payload = [spec.model_dump(mode="json") for spec in CovenantRegistry(store).list()]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("evaluate-all")
def evaluate_all_command(
    at_date: Annotated[datetime, typer.Option("--at-date", formats=["%Y-%m-%d"])],
    db_path: Annotated[Path, typer.Option("--db")] = Path("data/duckdb/hackathon.duckdb"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Evaluate every active borrower/covenant pair independently."""
    with DuckDBStore(db_path) as store:
        report = BatchEvaluationPipeline(store).run(at_date.date())
    payload = report.model_dump_json(indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    typer.echo(payload)


@app.command("serialize-submission")
def serialize_submission_command(
    results: Annotated[Path, typer.Option("--results", exists=True, dir_okay=False)],
    profile: Annotated[Path, typer.Option("--profile", exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Map internal results to an isolated strict submission profile."""
    try:
        raw = json.loads(results.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "results" in raw:
            raw = raw["results"]
        parsed = TypeAdapter(list[CovenantResult]).validate_python(raw)
        payload = SubmissionSerializer(load_submission_profile(profile)).serialize(parsed)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"Submission serialization failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(str(output.resolve()))


@app.command("validate-submission")
def validate_submission_command(
    submission: Annotated[Path, typer.Option("--submission", exists=True, dir_okay=False)],
    profile: Annotated[Path, typer.Option("--profile", exists=True, dir_okay=False)],
) -> None:
    """Validate a submission independently of evaluation."""
    try:
        payload = json.loads(submission.read_text(encoding="utf-8"))
        report = SubmissionValidator(load_submission_profile(profile)).validate(payload)
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"Submission validation failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(report.model_dump_json(indent=2))
    if not report.valid:
        raise typer.Exit(code=3)


@app.command("benchmark-full")
def benchmark_full_command(
    output: Annotated[Path, typer.Option("--output")] = Path("data/synthetic"),
) -> None:
    """Regenerate synthetic inputs and run the component-level benchmark."""
    generate_synthetic_dataset(output)
    report = run_benchmark(output)
    paths = write_benchmark_reports(report, output / "benchmark")
    typer.echo(
        json.dumps(
            {
                "summary": report.summary.model_dump(mode="json"),
                "reports": [str(path.resolve()) for path in paths],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("ocr-smoke")
def ocr_smoke_command() -> None:
    """Check that the local Paddle runtime can see the CUDA device."""
    try:
        import paddle

        payload = {
            "cuda_compiled": bool(paddle.device.is_compiled_with_cuda()),
            "device": paddle.device.get_device(),
            "gpu_count": int(paddle.device.cuda.device_count()),
        }
    except Exception as exc:
        typer.echo(f"OCR GPU smoke test failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(payload, indent=2))
    if not payload["cuda_compiled"] or payload["gpu_count"] < 1:
        raise typer.Exit(code=3)


if __name__ == "__main__":
    app()
