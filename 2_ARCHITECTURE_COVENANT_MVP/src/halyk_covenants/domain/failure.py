from enum import StrEnum


class FailureStage(StrEnum):
    """Stable stage labels for traces, local reports, and regression analysis."""

    OCR = "ocr"
    PARSING = "parsing"
    DETECTION = "detection"
    COMPILATION = "compilation"
    BORROWER_RESOLUTION = "borrower_resolution"
    TEMPORAL = "temporal"
    QUERY = "query"
    CALCULATION = "calculation"
    VERDICT = "verdict"
    EVIDENCE = "evidence"
    VERIFICATION = "verification"
    SERIALIZATION = "serialization"
