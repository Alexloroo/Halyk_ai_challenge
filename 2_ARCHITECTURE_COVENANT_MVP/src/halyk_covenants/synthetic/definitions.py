from datetime import date
from decimal import Decimal

from halyk_covenants.domain import (
    Borrower,
    ConditionSpec,
    CovenantSpec,
    EvidenceMode,
    FilterSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
    Transaction,
)
from halyk_covenants.synthetic.models import (
    BenchmarkCase,
    DocumentDefinition,
    ExpectedAnswer,
    SyntheticDatasetDefinition,
)

ALPHA_DOCUMENT = "alpha_trade_contract.pdf"
LIMITS_DOCUMENT = "borrower_limits_appendix.pdf"


def _filters(*specs: tuple[str, str, object]) -> list[FilterSpec]:
    return [
        FilterSpec(field=field, operator=operator, value=value)  # type: ignore[arg-type]
        for field, operator, value in specs
    ]


def _covenant(
    covenant_id: str,
    raw_text: str,
    borrower_id: str,
    metric_type: str,
    field: str,
    comparator: str,
    threshold: Decimal | int,
    document_file: str,
    *,
    filters: list[FilterSpec],
    evidence_mode: EvidenceMode = EvidenceMode.NONE,
    unit: str = "KZT",
) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        raw_text=raw_text,
        borrower_ids=[borrower_id],
        metric=MetricSpec(metric_type=metric_type, field=field, unit=unit),  # type: ignore[arg-type]
        condition=ConditionSpec(
            comparator=comparator,  # type: ignore[arg-type]
            threshold=threshold,
            unit=unit,
            currency="KZT" if unit == "KZT" else None,
        ),
        transaction_filters=filters,
        time_window=TimeWindowSpec(type="calendar_month"),
        evidence_mode=evidence_mode,
        effective_from=date(2026, 3, 15),
        source=SourceRef(document_id=document_file, page=1),
        confidence=1,
    )


def _expected(
    number: Decimal | int | None,
    verdict: str,
    status: str,
    explanation: str,
    evidence: str | None = None,
) -> ExpectedAnswer:
    return ExpectedAnswer(
        number=number,
        verdict=verdict,  # type: ignore[arg-type]
        evidence_transaction_id=evidence,
        status=status,  # type: ignore[arg-type]
        explanation=explanation,
    )


def build_synthetic_definition() -> SyntheticDatasetDefinition:
    alpha_filters = _filters(
        ("direction", "eq", "outgoing"),
        ("currency", "eq", "KZT"),
    )
    beta_filters = _filters(
        ("direction", "eq", "outgoing"),
        ("currency", "eq", "KZT"),
    )
    gamma_filters = _filters(
        ("direction", "eq", "outgoing"),
        ("currency", "eq", "KZT"),
    )
    covenants = [
        _covenant(
            "COV-ALPHA-SUM",
            "Совокупный месячный объём исходящих платежей не должен превышать 15 000 000 KZT.",
            "B001",
            "sum",
            "amount",
            "<=",
            Decimal("15000000"),
            ALPHA_DOCUMENT,
            filters=alpha_filters,
        ),
        _covenant(
            "COV-ALPHA-MAX",
            "Один исходящий перевод не может превышать 5 000 000 KZT.",
            "B001",
            "max",
            "amount",
            "<=",
            Decimal("5000000"),
            ALPHA_DOCUMENT,
            filters=alpha_filters,
            evidence_mode=EvidenceMode.VIOLATING_TRANSACTION,
        ),
        _covenant(
            "COV-ALPHA-COUNT",
            "Не более двух исходящих операций свыше 4 000 000 KZT в месяц.",
            "B001",
            "count",
            "transaction_id",
            "<=",
            2,
            ALPHA_DOCUMENT,
            filters=[*alpha_filters, FilterSpec(field="amount", operator="gt", value=4000000)],
            evidence_mode=EvidenceMode.TRIGGER_TRANSACTION,
            unit="count",
        ),
        _covenant(
            "COV-ALPHA-MIN",
            "Каждое входящее пополнение должно быть не менее 2 000 000 KZT.",
            "B001",
            "min",
            "amount",
            ">=",
            Decimal("2000000"),
            ALPHA_DOCUMENT,
            filters=_filters(
                ("direction", "eq", "incoming"),
                ("currency", "eq", "KZT"),
            ),
        ),
        _covenant(
            "COV-BETA-AVG",
            "Средний исходящий платёж Beta Logistics не более 4 000 000 KZT в месяц.",
            "B002",
            "avg",
            "amount",
            "<=",
            Decimal("4000000"),
            LIMITS_DOCUMENT,
            filters=beta_filters,
        ),
        _covenant(
            "COV-BETA-SUM",
            "Месячный исходящий объём Beta Logistics не более 12 000 000 KZT.",
            "B002",
            "sum",
            "amount",
            "<=",
            Decimal("12000000"),
            LIMITS_DOCUMENT,
            filters=beta_filters,
        ),
        _covenant(
            "COV-BETA-MAX",
            "Один исходящий платёж Beta Logistics не более 7 000 000 KZT.",
            "B002",
            "max",
            "amount",
            "<=",
            Decimal("7000000"),
            LIMITS_DOCUMENT,
            filters=beta_filters,
        ),
        _covenant(
            "COV-GAMMA-SUM",
            "Месячный исходящий объём заёмщика 000777 не более 6 000 000 KZT.",
            "000777",
            "sum",
            "amount",
            "<=",
            Decimal("6000000"),
            LIMITS_DOCUMENT,
            filters=gamma_filters,
        ),
    ]

    borrowers = [
        Borrower(
            borrower_id="B001",
            canonical_name="ТОО Альфа Трейд",
            aliases=["ALFA TRADE LLP", "ТОО «Альфа Трейд»"],
            identifiers={"synthetic": "SYN-BIN-0001"},
        ),
        Borrower(
            borrower_id="B002",
            canonical_name="Beta Logistics LLP",
            aliases=["Бета Лог.", "BETA LOGISTICS"],
            identifiers={"synthetic": "SYN-BIN-0002"},
        ),
        Borrower(
            borrower_id="000777",
            canonical_name="Gamma Retail Synthetic",
            aliases=["GAMMA RET."],
            identifiers={"synthetic": "SYN-BIN-0777"},
        ),
    ]

    transactions = [
        Transaction(
            transaction_id="BETA-003",
            borrower_id="B002",
            transaction_date=date(2026, 4, 20),
            amount=Decimal("6000000.000000"),
            currency="KZT",
            direction="outgoing",
            counterparty_name="Synthetic Carrier C",
            purpose="Freight settlement",
            source_row_id="ROW-001",
        ),
        Transaction(
            transaction_id="A003",
            borrower_id="B001",
            transaction_date=date(2026, 4, 20),
            amount=Decimal("5000000.000000"),
            currency="KZT",
            direction="outgoing",
            counterparty_name="Synthetic Vendor A",
            source_row_id="ROW-002",
        ),
        Transaction(
            transaction_id="A-USD",
            borrower_id="B001",
            transaction_date=date(2026, 4, 15),
            amount=Decimal("100.000000"),
            currency="USD",
            direction="outgoing",
            purpose="Foreign currency test row",
            source_row_id="ROW-003",
        ),
        Transaction(
            transaction_id="000001",
            borrower_id="000777",
            transaction_date=date(2026, 4, 1),
            amount=Decimal("1000000.000000"),
            currency="KZT",
            direction="outgoing",
            source_row_id="G-ROW-1",
        ),
        Transaction(
            transaction_id="A001",
            borrower_id="B001",
            transaction_date=date(2026, 4, 1),
            amount=Decimal("5000000.000000"),
            currency="KZT",
            direction="outgoing",
            counterparty_name="Synthetic Vendor A",
            purpose="Invoice 001",
            source_row_id="ROW-005",
        ),
        Transaction(
            transaction_id="BETA-IN",
            borrower_id="B002",
            transaction_date=date(2026, 4, 25),
            amount=Decimal("500000.000000"),
            currency="KZT",
            direction="incoming",
            purpose="Capital top-up",
            source_row_id="ROW-006",
        ),
        Transaction(
            transaction_id="000002",
            borrower_id="000777",
            transaction_date=date(2026, 4, 2),
            amount=Decimal("2000000.000000"),
            currency="KZT",
            direction="outgoing",
            source_row_id="G-ROW-2",
        ),
        Transaction(
            transaction_id="000003",
            borrower_id="000777",
            transaction_date=date(2026, 4, 3),
            amount=Decimal("2000000.000000"),
            currency="KZT",
            direction="outgoing",
            source_row_id="G-ROW-3",
        ),
        Transaction(
            transaction_id="000003",
            borrower_id="000777",
            transaction_date=date(2026, 4, 3),
            amount=Decimal("2000000.000000"),
            currency="KZT",
            direction="outgoing",
            source_row_id="G-ROW-3",
        ),
        Transaction(
            transaction_id="BETA-001",
            borrower_id="B002",
            transaction_date=date(2026, 4, 1),
            amount=Decimal("3000000.000000"),
            currency="KZT",
            direction="outgoing",
            counterparty_name="Synthetic Carrier A",
            source_row_id="ROW-010",
        ),
        Transaction(
            transaction_id="A002",
            borrower_id="B001",
            transaction_date=date(2026, 4, 10),
            amount=Decimal("6000000.000000"),
            currency="KZT",
            direction="outgoing",
            counterparty_name="Synthetic Vendor B",
            purpose="Invoice 002",
            source_row_id="ROW-011",
        ),
        Transaction(
            transaction_id="BETA-002",
            borrower_id="B002",
            transaction_date=date(2026, 4, 10),
            amount=Decimal("3000000.000000"),
            currency="KZT",
            direction="outgoing",
            counterparty_name="Synthetic Carrier B",
            source_row_id="ROW-012",
        ),
        Transaction(
            transaction_id="A004",
            borrower_id="B001",
            transaction_date=date(2026, 4, 22),
            amount=Decimal("2000000.000000"),
            currency="KZT",
            direction="incoming",
            purpose="Shareholder top-up",
            source_row_id="ROW-013",
        ),
        Transaction(
            transaction_id="A-MAY",
            borrower_id="B001",
            transaction_date=date(2026, 5, 1),
            amount=Decimal("9000000.000000"),
            currency="KZT",
            direction="outgoing",
            purpose="May boundary row",
            source_row_id="ROW-014",
        ),
    ]

    cases = [
        BenchmarkCase(
            case_id="ALPHA-SUM-APRIL",
            question="Соблюдён ли месячный лимит исходящих KZT-платежей Alpha Trade за апрель?",
            covenant_id="COV-ALPHA-SUM",
            borrower_id="B001",
            evaluation_date=date(2026, 4, 30),
            document_file=ALPHA_DOCUMENT,
            expected=_expected(
                Decimal("16000000.000000"),
                "violated",
                "success",
                "5M + 6M + 5M = 16M KZT; USD row and May row are excluded.",
            ),
        ),
        BenchmarkCase(
            case_id="ALPHA-MAX-APRIL",
            question="Каков максимальный исходящий KZT-перевод Alpha Trade за апрель?",
            covenant_id="COV-ALPHA-MAX",
            borrower_id="B001",
            evaluation_date=date(2026, 4, 30),
            document_file=ALPHA_DOCUMENT,
            expected=_expected(
                Decimal("6000000.000000"),
                "violated",
                "success",
                "A002 is the largest matching transfer and exceeds 5M KZT.",
                "A002",
            ),
        ),
        BenchmarkCase(
            case_id="ALPHA-COUNT-TRIGGER",
            question=(
                "Какая операция третьей превысила месячный лимит операций Alpha Trade свыше 4M?"
            ),
            covenant_id="COV-ALPHA-COUNT",
            borrower_id="B001",
            evaluation_date=date(2026, 4, 30),
            document_file=ALPHA_DOCUMENT,
            expected=_expected(
                3,
                "violated",
                "success",
                "A001, A002, and A003 match; A003 is the threshold-crossing third transaction.",
                "A003",
            ),
        ),
        BenchmarkCase(
            case_id="ALPHA-MIN-INCOMING",
            question="Соблюдён ли минимальный размер входящего пополнения Alpha Trade за апрель?",
            covenant_id="COV-ALPHA-MIN",
            borrower_id="B001",
            evaluation_date=date(2026, 4, 30),
            document_file=ALPHA_DOCUMENT,
            expected=_expected(
                Decimal("2000000.000000"),
                "complied",
                "success",
                "The only matching incoming KZT transaction equals the 2M boundary.",
            ),
        ),
        BenchmarkCase(
            case_id="BETA-AVG-APRIL",
            question="Превысил ли средний исходящий платёж Beta Logistics 4M KZT в апреле?",
            covenant_id="COV-BETA-AVG",
            borrower_id="B002",
            evaluation_date=date(2026, 4, 30),
            document_file=LIMITS_DOCUMENT,
            expected=_expected(
                Decimal("4000000.000000"),
                "complied",
                "success",
                "(3M + 3M + 6M) / 3 equals the permitted 4M boundary.",
            ),
        ),
        BenchmarkCase(
            case_id="BETA-SUM-BOUNDARY",
            question="Соблюдён ли суммарный лимит Beta Logistics за апрель?",
            covenant_id="COV-BETA-SUM",
            borrower_id="B002",
            evaluation_date=date(2026, 4, 30),
            document_file=LIMITS_DOCUMENT,
            expected=_expected(
                Decimal("12000000.000000"),
                "complied",
                "success",
                "The total equals 12M and <= is satisfied at the boundary.",
            ),
        ),
        BenchmarkCase(
            case_id="ALPHA-SUM-EMPTY",
            question="Каков исходящий объём Alpha Trade за июнь при отсутствии операций?",
            covenant_id="COV-ALPHA-SUM",
            borrower_id="B001",
            evaluation_date=date(2026, 6, 30),
            document_file=ALPHA_DOCUMENT,
            expected=_expected(
                Decimal("0.000000"),
                "complied",
                "success",
                "Empty SUM has explicit zero semantics.",
            ),
        ),
        BenchmarkCase(
            case_id="ALPHA-MAX-EMPTY",
            question="Каков максимальный перевод Alpha Trade за июнь при отсутствии операций?",
            covenant_id="COV-ALPHA-MAX",
            borrower_id="B001",
            evaluation_date=date(2026, 6, 30),
            document_file=ALPHA_DOCUMENT,
            expected=_expected(
                None,
                "unknown",
                "partial",
                "Empty MAX is undefined and must remain an explicit partial result.",
            ),
        ),
        BenchmarkCase(
            case_id="GAMMA-SUM-DUPLICATE",
            question="Каков исходящий объём 000777 с сохранённой дублированной строкой?",
            covenant_id="COV-GAMMA-SUM",
            borrower_id="000777",
            evaluation_date=date(2026, 4, 30),
            document_file=LIMITS_DOCUMENT,
            expected=_expected(
                Decimal("7000000.000000"),
                "violated",
                "success",
                "1M + 2M + 2M + duplicated 2M = 7M; ingestion does not silently deduplicate.",
            ),
        ),
        BenchmarkCase(
            case_id="BETA-MAX-ISOLATION",
            question="Не смешивает ли расчёт максимума Beta Logistics операции других заёмщиков?",
            covenant_id="COV-BETA-MAX",
            borrower_id="B002",
            evaluation_date=date(2026, 4, 30),
            document_file=LIMITS_DOCUMENT,
            expected=_expected(
                Decimal("6000000.000000"),
                "complied",
                "success",
                "Only B002 rows are considered; its maximum is 6M against a 7M limit.",
            ),
        ),
    ]

    documents = [
        DocumentDefinition(
            file_name=ALPHA_DOCUMENT,
            title="Синтетический договор — ТОО «Альфа Трейд»",
            borrower_ids=["B001"],
            covenant_ids=[
                "COV-ALPHA-SUM",
                "COV-ALPHA-MAX",
                "COV-ALPHA-COUNT",
                "COV-ALPHA-MIN",
            ],
            defects=[
                "borrower name varies between Cyrillic and Latin forms",
                "threshold is split across a visual line break",
                "noncritical Russian word contains a typo",
                "numeric formatting mixes comma and spaces",
                "effective date is separated from its qualifying rule",
                "footer is positioned close to body text",
            ],
        ),
        DocumentDefinition(
            file_name=LIMITS_DOCUMENT,
            title="Синтетическое приложение — лимиты заёмщиков",
            borrower_ids=["B002", "000777"],
            covenant_ids=[
                "COV-BETA-AVG",
                "COV-BETA-SUM",
                "COV-BETA-MAX",
                "COV-GAMMA-SUM",
            ],
            defects=[
                "table cells contain wrapped text",
                "borrower name is abbreviated in one row",
                "table header is repeated on a second page",
                "exception is expressed as a footnote below the table",
                "optional currency cell is blank",
                "one threshold uses the textual unit млн KZT",
            ],
        ),
    ]

    anomalies = [
        {
            "anomaly_id": "ANOM-001",
            "location": "transactions.transaction_id=000003",
            "description": "Exact duplicate row is retained.",
            "expected_behavior": "Both rows contribute to aggregates and share a source hash.",
        },
        {
            "anomaly_id": "ANOM-002",
            "location": "transactions.transaction_id=A-USD",
            "description": "Different-currency row is present.",
            "expected_behavior": "KZT covenant filters exclude the USD row.",
        },
        {
            "anomaly_id": "ANOM-003",
            "location": "transactions row order",
            "description": "Rows are not sorted chronologically.",
            "expected_behavior": "Calendar filters and evidence ordering remain deterministic.",
        },
        {
            "anomaly_id": "ANOM-004",
            "location": "optional transaction fields",
            "description": "Counterparty and purpose values are intentionally sparse.",
            "expected_behavior": "Optional nulls do not block canonical ingestion.",
        },
    ]

    return SyntheticDatasetDefinition(
        dataset_version="2026.08.02-v1",
        documents=documents,
        borrowers=borrowers,
        transactions=transactions,
        covenants=covenants,
        cases=cases,
        known_anomalies=anomalies,
    )
