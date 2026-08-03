from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from pydantic import TypeAdapter, ValidationError

from halyk_covenants.config import load_settings
from halyk_covenants.documents.retrieval import SentenceTransformerEmbeddingProvider
from halyk_covenants.llm import DeepSeekChatFactory, DeepSeekConfigurationError
from halyk_covenants.pipeline import BatchEvaluationReport, ReviewPipeline, ReviewedBatchReport
from halyk_covenants.review import (
    LangChainReviewer,
    ReviewEmbeddingProvider,
    Reviewer,
    ReviewService,
    SimilarReviewCase,
    SimilarityRetriever,
)
from halyk_covenants.storage import DuckDBStore

app = typer.Typer(
    name="halyk-review",
    help="Review deterministic covenant answers and use cosine fallback for uncertain cases.",
)


def run_review(
    *,
    batch: BatchEvaluationReport,
    store: DuckDBStore,
    reviewer: Reviewer,
    embedder: ReviewEmbeddingProvider,
    corpus: list[SimilarReviewCase],
    confidence_threshold: Decimal = Decimal("0.70"),
    compiler_confidence_threshold: Decimal = Decimal("0.70"),
    top_k: int = 5,
    minimum_similarity: float = 0.55,
) -> ReviewedBatchReport:
    retriever = SimilarityRetriever(corpus, embedder)
    service = ReviewService(
        reviewer=reviewer,
        similarity_retriever=retriever,
        confidence_threshold=confidence_threshold,
        compiler_confidence_threshold=compiler_confidence_threshold,
        similarity_top_k=top_k,
        minimum_similarity=minimum_similarity,
    )
    return ReviewPipeline(store, service=service).run(batch)


@app.command("review-results")
def review_results_command(
    results: Annotated[
        Path,
        typer.Option("--results", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    review_corpus: Annotated[
        Path,
        typer.Option(
            "--review-corpus", exists=True, file_okay=True, dir_okay=False, readable=True
        ),
    ],
    output: Annotated[Path, typer.Option("--output")],
    db_path: Annotated[Path, typer.Option("--db")] = Path("data/duckdb/hackathon.duckdb"),
    at_date: Annotated[
        datetime | None,
        typer.Option("--at-date", formats=["%Y-%m-%d"]),
    ] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    confidence_threshold: Annotated[
        float,
        typer.Option("--confidence-threshold", min=0.0, max=1.0),
    ] = 0.70,
    compiler_confidence_threshold: Annotated[
        float,
        typer.Option("--compiler-confidence-threshold", min=0.0, max=1.0),
    ] = 0.70,
    top_k: Annotated[int, typer.Option("--top-k", min=1)] = 5,
    minimum_similarity: Annotated[
        float,
        typer.Option("--minimum-similarity", min=-1.0, max=1.0),
    ] = 0.55,
    embedding_model: Annotated[
        str,
        typer.Option("--embedding-model"),
    ] = "intfloat/multilingual-e5-small",
) -> None:
    """Review an evaluate-all report before final submission serialization."""
    try:
        batch = BatchEvaluationReport.model_validate_json(results.read_text(encoding="utf-8"))
        if at_date is not None and at_date.date() != batch.evaluation_date:
            raise ValueError(
                f"--at-date {at_date.date()} does not match results date {batch.evaluation_date}"
            )
        raw_corpus = json.loads(review_corpus.read_text(encoding="utf-8"))
        corpus = TypeAdapter(list[SimilarReviewCase]).validate_python(raw_corpus)
        settings = load_settings(config_path)
        model = DeepSeekChatFactory(settings.deepseek).create()
        reviewer = LangChainReviewer(model)
        embedder = SentenceTransformerEmbeddingProvider(embedding_model)
        with DuckDBStore(db_path) as store:
            report = run_review(
                batch=batch,
                store=store,
                reviewer=reviewer,
                embedder=embedder,
                corpus=corpus,
                confidence_threshold=Decimal(str(confidence_threshold)),
                compiler_confidence_threshold=Decimal(str(compiler_confidence_threshold)),
                top_k=top_k,
                minimum_similarity=minimum_similarity,
            )
    except (
        DeepSeekConfigurationError,
        OSError,
        RuntimeError,
        ValidationError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        typer.echo(f"Review failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(report.model_dump_json(indent=2))
