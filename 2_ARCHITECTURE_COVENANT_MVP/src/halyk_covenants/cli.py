import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from pydantic import TypeAdapter, ValidationError

from halyk_covenants.ask import Route, render, route_question
from halyk_covenants.benchmark.reporting import write_benchmark_reports
from halyk_covenants.benchmark.runner import run_benchmark
from halyk_covenants.config import load_settings
from halyk_covenants.covenants import (
    CompilerGraph,
    CovenantCompiler,
    CovenantRegistry,
    LangChainCompilerRepairer,
)
from halyk_covenants.domain import CovenantResult, CovenantSpec, TimeWindowSpec
from halyk_covenants.evaluators import EvaluationService, TemporalEvaluationService
from halyk_covenants.ingestion import PageQualityRouter, PDFIngestor
from halyk_covenants.llm import DeepSeekChatFactory, DeepSeekConfigurationError
from halyk_covenants.logging import configure_logging
from halyk_covenants.ocr import PaddleOCRProvider
from halyk_covenants.pipeline import BatchEvaluationPipeline, PreprocessPipeline
from halyk_covenants.pipeline.preprocess import STRUCTURED_SUFFIXES
from halyk_covenants.reporting import AnswerReportBuilder, load_confidence, render_html
from halyk_covenants.review import LangChainSpecReviewer, SpecReviewService
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.submission import (
    SubmissionSerializer,
    SubmissionValidator,
    load_submission_profile,
)
from halyk_covenants.synthetic.generator import generate_synthetic_dataset
from halyk_covenants.synthetic.validation import DatasetValidationError
from halyk_covenants.verification import (
    ManifestBuilder,
    build_confidence_report,
    compute_confidence,
)
from halyk_covenants.vlm import PaddleLayoutProvider


def resolve_document_name(store: DuckDBStore, spec: CovenantSpec | None) -> str | None:
    """Map a content-addressed document_id back to its file name for display.

    The id is a SHA-256 of the file contents, which is right for provenance and
    useless to a reader; documents.source_path holds the original path.
    """
    if spec is None or spec.source is None or not spec.source.document_id:
        return None
    row = store.connection.execute(
        "SELECT source_path FROM documents WHERE document_id = ?",
        [spec.source.document_id],
    ).fetchone()
    return Path(row[0]).name if row and row[0] else None


def apply_question_period(
    versions: list[CovenantSpec], route: "Route"
) -> list[CovenantSpec]:
    """Narrow covenants that define no period of their own to the asked-about span.

    A covenant with its own window ("за календарный месяц") keeps it — the
    question only selects which month. A covenant without one constrains every
    transaction individually, so "в апреле" is a genuine narrowing rather than a
    contradiction, and dropping it would answer a different question.
    """
    if route.period is None:
        return versions
    start, end = route.period
    narrowed: list[CovenantSpec] = []
    for spec in versions:
        if spec.time_window is not None and spec.time_window.type != "none":
            narrowed.append(spec)
            continue
        narrowed.append(
            spec.model_copy(
                update={
                    "time_window": TimeWindowSpec(
                        type="custom", start_date=start, end_date=end
                    )
                }
            )
        )
        route.period_applied = True
    return narrowed


def load_manifest_questions(path: Path | None) -> dict[tuple[str, str], str]:
    """Load organizer questions as an independent source for the expectation manifest."""
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    questions: dict[tuple[str, str], str] = {}
    for record in raw:
        key = (str(record["borrower_id"]), str(record["covenant_id"]))
        questions[key] = str(record.get("question", ""))
    return questions

app = typer.Typer(
    name="halyk-covenants",
    help="Load structured transactions and evaluate deterministic covenant specifications.",
    no_args_is_help=True,
)


@app.callback()
def main(
    log_level: Annotated[str, typer.Option("--log-level")] = "WARNING",
    env_file: Annotated[Path | None, typer.Option("--env-file")] = None,
) -> None:
    """Halyk covenant evaluation MVP."""
    # Outside the container nothing else loads .env, so DEEPSEEK_API_KEY and the
    # LANGSMITH_* variables would be missing. Real environment variables win.
    load_dotenv(dotenv_path=env_file, override=False)
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
    spec_review: Annotated[bool, typer.Option("--spec-review/--no-spec-review")] = True,
) -> None:
    """Ingest structured files and compile PDF covenants through DeepSeek/LangGraph."""
    try:
        settings = load_settings(config_path)
        compiler_graph = None
        spec_review_service = None
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
            if spec_review:
                spec_review_service = SpecReviewService(
                    reviewer=LangChainSpecReviewer(model),
                    compiler_graph=compiler_graph,
                )
        with DuckDBStore(db_path) as store:
            report = PreprocessPipeline(
                store,
                pdf_ingestor=pdf_ingestor,
                compiler_graph=compiler_graph,
                spec_review_service=spec_review_service,
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
    questions_path: Annotated[
        Path | None,
        typer.Option("--questions", exists=True, file_okay=True, dir_okay=False, readable=True),
    ] = None,
    confidence_output: Annotated[Path | None, typer.Option("--confidence-output")] = None,
    use_manifest: Annotated[bool, typer.Option("--manifest/--no-manifest")] = True,
) -> None:
    """Evaluate every active borrower/covenant pair independently."""
    with DuckDBStore(db_path) as store:
        registry = CovenantRegistry(store)
        manifest = None
        if use_manifest:
            questions = load_manifest_questions(questions_path)
            manifest = ManifestBuilder(store, registry).build(questions)
        report = BatchEvaluationPipeline(store, registry, manifest=manifest).run(at_date.date())

        if confidence_output is not None:
            specs = {spec.covenant_id: spec for spec in registry.list()}
            for spec in registry.list():
                group_id = spec.covenant_group_id or spec.covenant_id
                specs.setdefault(group_id, spec)
            flags: dict[tuple[str, str], set[str]] = {}
            for issue in report.verification.issues:
                if issue.borrower_id and issue.covenant_id:
                    flags.setdefault((issue.borrower_id, issue.covenant_id), set()).add(issue.code)
            entries = build_confidence_report(report.results, specs, flags)
            confidence_output.parent.mkdir(parents=True, exist_ok=True)
            confidence_output.write_text(
                json.dumps(
                    [entry.model_dump(mode="json") for entry in entries],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    payload = report.model_dump_json(indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    typer.echo(payload)


@app.command("run")
def run_command(
    input_root: Annotated[Path, typer.Argument()] = Path("input"),
    db_path: Annotated[Path, typer.Option("--db")] = Path("data/run.duckdb"),
    at_date: Annotated[
        datetime | None, typer.Option("--at-date", formats=["%Y-%m-%d"])
    ] = None,
    out_dir: Annotated[Path, typer.Option("--out")] = Path("data"),
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    enable_ocr: Annotated[bool, typer.Option("--ocr/--no-ocr")] = False,
) -> None:
    """Обработать папку с данными и посчитать все ковенанты — одной командой."""
    problems = _check_input(input_root)
    if problems:
        typer.echo("Входные данные не готовы:\n", err=True)
        for problem in problems:
            typer.echo(f"  • {problem}", err=True)
        typer.echo(
            f"\nСмотрите {input_root / 'КАК_ПОЛЬЗОВАТЬСЯ.txt'}",
            err=True,
        )
        raise typer.Exit(code=2)

    questions_file = input_root / "questions.json"
    questions_path = questions_file if questions_file.exists() else None
    evaluation_date = at_date.date() if at_date else date.today()
    out_dir.mkdir(parents=True, exist_ok=True)

    typer.echo("[1/3] Чтение документов и компиляция ковенантов…")
    preprocess_command(
        input_root=input_root,
        db_path=db_path,
        config_path=config_path,
        enable_ocr=enable_ocr,
        spec_review=True,
    )

    typer.echo(f"\n[2/3] Оценка на {evaluation_date}…")
    evaluate_all_command(
        at_date=datetime.combine(evaluation_date, datetime.min.time()),
        db_path=db_path,
        output=out_dir / "results.json",
        questions_path=questions_path,
        confidence_output=out_dir / "confidence.json",
        use_manifest=True,
    )

    typer.echo("\n[3/3] Отчёт…")
    answer_report_command(
        results=out_dir / "results.json",
        output=out_dir / "answers.html",
        db_path=db_path,
        questions_path=questions_path,
        confidence_path=out_dir / "confidence.json",
    )

    typer.echo("\nГотово. Что дальше:")
    typer.echo(f"  {out_dir / 'answers.html'}     открыть в браузере")
    typer.echo(f"  {out_dir / 'confidence.json'}  сомнительное сверху")
    typer.echo('  .\\ask.bat "ваш вопрос"        спросить своими словами')


def _check_input(root: Path) -> list[str]:
    """Fail with a sentence a person can act on, not a traceback."""
    if not root.exists():
        return [f"Папки {root} нет. Создайте её и положите данные."]

    problems: list[str] = []
    files = [p for p in root.rglob("*") if p.is_file()]
    pdfs = [p for p in files if p.suffix.casefold() == ".pdf"]
    tables = [p for p in files if p.suffix.casefold() in STRUCTURED_SUFFIXES]

    if not pdfs:
        problems.append(f"Нет ни одного PDF. Положите договоры в {root / 'documents'}")
    if not tables:
        problems.append(
            f"Нет ни одной таблицы CSV/XLSX. Положите их в {root / 'transactions'}"
        )

    questions = root / "questions.json"
    if questions.exists():
        try:
            raw = json.loads(questions.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                problems.append("questions.json должен быть списком в квадратных скобках []")
            else:
                for index, record in enumerate(raw, start=1):
                    missing = {"borrower_id", "covenant_id"} - set(record)
                    if missing:
                        problems.append(
                            f"questions.json, запись {index}: нет полей "
                            f"{', '.join(sorted(missing))}"
                        )
                        break
        except json.JSONDecodeError as exc:
            problems.append(f"questions.json — сломанный JSON: {exc}")
    return problems


@app.command("ask")
def ask_command(
    question: Annotated[str, typer.Argument(help="Вопрос обычным текстом")],
    db_path: Annotated[Path, typer.Option("--db")] = Path("data/duckdb/hackathon.duckdb"),
    at_date: Annotated[
        datetime | None, typer.Option("--at-date", formats=["%Y-%m-%d"])
    ] = None,
    borrower: Annotated[str | None, typer.Option("--borrower")] = None,
    covenant: Annotated[str | None, typer.Option("--covenant")] = None,
) -> None:
    """Ответить на вопрос обычным текстом, показав, откуда взялся ответ."""
    default_date = at_date.date() if at_date else date.today()
    try:
        with DuckDBStore(db_path) as store:
            specs = CovenantRegistry(store).list()
            if not specs:
                typer.echo(
                    "В базе нет скомпилированных ковенантов. "
                    "Сначала выполните: halyk-covenants preprocess <папка>",
                    err=True,
                )
                raise typer.Exit(code=2)

            route = route_question(
                question,
                store.list_borrowers(),
                specs,
                default_date=default_date,
                borrower_id=borrower,
                covenant_id=covenant,
            )

            result = calculation = evidence = None
            confidence = "medium"
            document_name = None
            if route.covenant is not None and route.borrower_id:
                versions = [
                    s
                    for s in specs
                    if (s.covenant_group_id or s.covenant_id)
                    == (route.covenant.covenant_group_id or route.covenant.covenant_id)
                ]
                versions = apply_question_period(versions, route)
                route.covenant = next(
                    (v for v in versions if v.covenant_id == route.covenant.covenant_id),
                    route.covenant,
                )
                document_name = resolve_document_name(store, route.covenant)
                result = TemporalEvaluationService(EvaluationService(store)).evaluate_versions(
                    versions, route.borrower_id, route.at_date
                )
                builder = AnswerReportBuilder(store)
                calculation = builder._load_calculation(result)
                evidence = builder._load_transaction(result.evidence_transaction_id)
                confidence = compute_confidence(result, route.covenant, set())
            elif not route.problems:
                route.problems.append("Не удалось определить пару заёмщик/ковенант.")
    except typer.Exit:
        raise
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"Не удалось ответить: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(
        render(question, route, result, calculation, evidence, confidence, document_name)
    )
    if result is None:
        raise typer.Exit(code=1)


@app.command("answer-report")
def answer_report_command(
    results: Annotated[Path, typer.Option("--results", exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output")] = Path("data/answers.html"),
    db_path: Annotated[Path, typer.Option("--db")] = Path("data/duckdb/hackathon.duckdb"),
    questions_path: Annotated[
        Path | None,
        typer.Option("--questions", exists=True, file_okay=True, dir_okay=False),
    ] = None,
    confidence_path: Annotated[
        Path | None, typer.Option("--confidence", exists=True, dir_okay=False)
    ] = None,
) -> None:
    """Render a readable answer report: verdict, number, evidence and source clause."""
    try:
        payload = json.loads(results.read_text(encoding="utf-8"))
        evaluation_date = datetime.fromisoformat(
            payload.get("evaluation_date", str(date.today()))
        ).date() if isinstance(payload, dict) else date.today()
        raw_results = payload["results"] if isinstance(payload, dict) else payload
        parsed = TypeAdapter(list[CovenantResult]).validate_python(raw_results)

        questions = load_manifest_questions(questions_path)
        confidence = load_confidence(confidence_path)

        with DuckDBStore(db_path) as store:
            specs = {}
            for spec in CovenantRegistry(store).list():
                specs[spec.covenant_id] = spec
                specs.setdefault(spec.covenant_group_id or spec.covenant_id, spec)
            cards = AnswerReportBuilder(store).build_cards(
                parsed, specs, questions, confidence
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_html(cards, evaluation_date), encoding="utf-8")
    except (OSError, ValueError, ValidationError, KeyError) as exc:
        typer.echo(f"Answer report failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"Отчёт готов: {output.resolve()}")
    typer.echo(f"Ответов: {len(cards)}")


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
