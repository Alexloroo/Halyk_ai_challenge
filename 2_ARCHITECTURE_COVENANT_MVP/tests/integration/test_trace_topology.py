from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.pipeline import BatchEvaluationPipeline, PreprocessPipeline


def test_major_pipeline_entrypoints_are_langsmith_traceable() -> None:
    assert hasattr(PreprocessPipeline.run, "__wrapped__")
    assert hasattr(BatchEvaluationPipeline.run, "__wrapped__")
    assert hasattr(EvaluationService.evaluate, "__wrapped__")
