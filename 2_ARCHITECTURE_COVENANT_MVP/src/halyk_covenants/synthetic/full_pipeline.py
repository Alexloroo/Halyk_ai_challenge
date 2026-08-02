from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from halyk_covenants.covenants import (
    CompilerGraph,
    CovenantCompiler,
    LangChainCompilerRepairer,
)
from halyk_covenants.pipeline import (
    BatchEvaluationPipeline,
    BatchEvaluationReport,
    PreprocessPipeline,
    PreprocessReport,
)
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.submission import SubmissionProfile, SubmissionSerializer, SubmissionValidator
from halyk_covenants.synthetic.generator import generate_synthetic_dataset


class FullSyntheticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preprocessing: PreprocessReport
    evaluation: BatchEvaluationReport
    submission: dict[str, object]


def run_full_synthetic_pipeline(
    output_root: Path,
    *,
    model: Any,
    at_date: date = date(2026, 4, 30),
    profile: SubmissionProfile | None = None,
) -> FullSyntheticReport:
    dataset = output_root / "dataset"
    generate_synthetic_dataset(dataset)
    graph = CompilerGraph(
        compiler=CovenantCompiler(model),
        repairer=LangChainCompilerRepairer(model),
    )
    with DuckDBStore(output_root / "pipeline.duckdb") as store:
        preprocessing = PreprocessPipeline(store, compiler_graph=graph).run(dataset)
        evaluation = BatchEvaluationPipeline(store).run(at_date)
    active_profile = profile or SubmissionProfile(
        name="synthetic",
        ratio_representation="percentage",
        verdict_labels={
            "complied": "COMPLIED",
            "violated": "VIOLATED",
            "unknown": "UNKNOWN",
        },
    )
    submission = SubmissionSerializer(active_profile).serialize(evaluation.results)
    validation = SubmissionValidator(active_profile).validate(submission)
    if not validation.valid:
        raise ValueError(f"invalid synthetic submission: {validation.errors}")
    return FullSyntheticReport(
        preprocessing=preprocessing,
        evaluation=evaluation,
        submission=submission,
    )
