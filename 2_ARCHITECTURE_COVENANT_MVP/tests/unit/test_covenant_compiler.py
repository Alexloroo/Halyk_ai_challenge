from decimal import Decimal

from halyk_covenants.covenants import CovenantCandidate, CovenantCompiler
from halyk_covenants.domain import SourceRef


class FakeRunnable:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.invocations: list[object] = []

    def invoke(self, prompt: object) -> dict[str, object]:
        self.invocations.append(prompt)
        return self.payload


class FakeStructuredModel:
    def __init__(self, payload: dict[str, object]) -> None:
        self.runnable = FakeRunnable(payload)
        self.schema: type[object] | None = None
        self.method: str | None = None

    def with_structured_output(self, schema: type[object], *, method: str) -> FakeRunnable:
        self.schema = schema
        self.method = method
        return self.runnable


def candidate() -> CovenantCandidate:
    return CovenantCandidate(
        candidate_id="candidate-1",
        raw_text="Monthly outgoing payments must not exceed 10,000,000 KZT.",
        ordinal=1,
        borrower_ids=["B001"],
        source=SourceRef(document_id="contract", page=1),
        confidence=Decimal("0.95"),
    )


def valid_spec(*, currency: str | None = "KZT") -> dict[str, object]:
    return {
        "covenant_id": "COV-1",
        "raw_text": candidate().raw_text,
        "borrower_ids": ["B001"],
        "metric": {"metric_type": "sum", "field": "amount", "unit": "money"},
        "condition": {
            "comparator": "<=",
            "threshold": "10000000",
            "unit": "money",
            "currency": currency,
        },
        "transaction_filters": [
            {"field": "direction", "operator": "eq", "value": "outgoing"},
            {"field": "currency", "operator": "eq", "value": "KZT"},
        ],
        "time_window": {"type": "calendar_month"},
        "evidence_mode": "none",
        "source": {"document_id": "contract", "page": 1},
        "confidence": 0.92,
    }


def test_compiler_accepts_valid_structured_spec() -> None:
    model = FakeStructuredModel({"specs": [valid_spec()]})

    outcome = CovenantCompiler(model=model).compile(candidate(), context="Definitions: none")

    assert outcome.route == "straightforward"
    assert outcome.validation_errors == []
    assert outcome.specs[0].condition.threshold == Decimal("10000000")
    assert model.schema is not None
    assert model.method == "json_mode"
    assert model.runnable.invocations
    prompt_text = "\n".join(
        str(getattr(message, "content", message)) for message in model.runnable.invocations[0]
    )
    assert "top-level key must be `specs`" in prompt_text
    assert '"metric_type"' in prompt_text


def test_compiler_routes_missing_explicit_currency_to_ambiguous() -> None:
    model = FakeStructuredModel({"specs": [valid_spec(currency=None)]})

    outcome = CovenantCompiler(model=model).compile(candidate(), context="Definitions: none")

    assert outcome.route == "ambiguous"
    assert any("currency" in error for error in outcome.validation_errors)


def test_compiler_does_not_accept_unknown_transaction_field() -> None:
    draft = valid_spec()
    draft["transaction_filters"] = [
        {"field": "invented_category", "operator": "eq", "value": "tax"}
    ]

    outcome = CovenantCompiler(model=FakeStructuredModel({"specs": [draft]})).compile(
        candidate(), context=""
    )

    assert outcome.route == "ambiguous"
    assert any("invented_category" in error for error in outcome.validation_errors)


def test_compiler_overlays_resolved_scope_and_source_provenance() -> None:
    draft = valid_spec()
    draft["borrower_ids"] = ["INVENTED"]
    draft["raw_text"] = "model paraphrase"
    draft["source"] = {"document_id": None, "page": None}

    outcome = CovenantCompiler(model=FakeStructuredModel({"specs": [draft]})).compile(
        candidate(), context=""
    )

    assert outcome.route == "straightforward"
    assert outcome.specs[0].borrower_ids == ["B001"]
    assert outcome.specs[0].raw_text == candidate().raw_text
    assert outcome.specs[0].source == candidate().source


def test_compiler_routes_missing_direction_and_currency_filters_to_repair() -> None:
    draft = valid_spec()
    draft["transaction_filters"] = []

    outcome = CovenantCompiler(model=FakeStructuredModel({"specs": [draft]})).compile(
        candidate(), context=""
    )

    assert outcome.route == "ambiguous"
    assert any("direction=outgoing" in error for error in outcome.validation_errors)
    assert any("currency=KZT" in error for error in outcome.validation_errors)


def test_compiler_turns_structured_parse_failure_into_ambiguous_outcome() -> None:
    class RaisingRunnable:
        def invoke(self, prompt: object) -> object:
            del prompt
            raise ValueError("invalid model JSON")

    class RaisingModel:
        def with_structured_output(self, schema: type[object], *, method: str) -> RaisingRunnable:
            del schema, method
            return RaisingRunnable()

    outcome = CovenantCompiler(model=RaisingModel()).compile(candidate(), context="")

    assert outcome.route == "ambiguous"
    assert outcome.specs == []
    assert outcome.validation_errors == [
        "structured output parsing failed: ValueError: invalid model JSON"
    ]
