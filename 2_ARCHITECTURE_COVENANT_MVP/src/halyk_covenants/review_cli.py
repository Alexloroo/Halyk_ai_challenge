from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from halyk_covenants.config import load_settings
from halyk_covenants.documents.retrieval import SentenceTransformerEmbeddingProvider
from halyk_covenants.llm import DeepSeekChatFactory, DeepSeekConfigurationError
from halyk_covenants.pipeline import BatchEvaluationReport, ReviewedBatchReport, ReviewPipeline
from halyk_covenants.review import (
    ReviewEmbeddingProvider,
    Reviewer,
    ReviewService,
    SimilarityRetriever,
    SimilarReviewCase,
)
from halyk_covenants.review.langchain_reviewer import LangChainReviewer
from halyk_covenants.storage import DuckDBStore

app = typer.Typer(
    name="halyk-review",
    help="Review deterministic covenant answers and use cosine fallback for uncertain cases.",
)


class ReviewQuestionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_id: str
    covenant_id: str
    question: str


def load_questions(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = TypeAdapter(list[ReviewQuestionRecord]).validate_python(raw)
    questions: dict[tuple[str, str], str] = {}
    for record in records:
        key = (record.borrower_id, record.covenant_id)
        if key in questions:
            raise ValueError(
                f"duplicate review question for {record.borrower_id}/{record.covenant_id}"
            )
        questions[key] = record.question
    return questions


def run_review(
    *,
    batch: BatchEvaluationReport,
    store: DuckDBStore,
    reviewer: Reviewer,
    embedder: ReviewEmbeddingProvider,
    corpus: list[SimilarReviewCase],
    questions: Mapping[tuple[str, str], str] | None = None,
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
    return ReviewPipeline(store, service=service).run(batch, questions=questions)


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
    questions_path: Annotated[
        Path | None,
        typer.Option("--questions", exists=True, file_okay=True, dir_okay=False, readable=True),
    ] = None,
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
        questions = load_questions(questions_path)
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
                questions=questions,
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
