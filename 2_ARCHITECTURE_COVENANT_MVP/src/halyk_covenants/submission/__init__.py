from pathlib import Path

import yaml

from .models import SubmissionProfile, SubmissionValidationReport
from .serializer import SubmissionSerializer
from .validator import SubmissionValidator


def load_submission_profile(path: Path) -> SubmissionProfile:
    with path.open(encoding="utf-8") as stream:
        return SubmissionProfile.model_validate(yaml.safe_load(stream) or {})


__all__ = [
    "SubmissionProfile",
    "SubmissionSerializer",
    "SubmissionValidationReport",
    "SubmissionValidator",
    "load_submission_profile",
]
