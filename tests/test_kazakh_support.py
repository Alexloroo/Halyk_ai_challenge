from __future__ import annotations

from decimal import Decimal

from halyk.audit import AdjustmentKind, extract_adjustments
from halyk.categorize import Category, categorize
from halyk.docs import DocKind, Edition, _classify
from halyk.parties import extract_related_parties
from halyk.rules import RuleKind, extract_rules


def test_kazakh_credit_agreement_classification_and_rules() -> None:
    text = """
ОРЫНДАУ ДАНАСЫ
БАНКТІК ҚАРЫЗ ШАРТЫ
Account ID: ACC-9901
6-бап. Қаржылық ковенанттар
6.1-тармақ Ең төменгі түсім.
2025-01-01 бастап 2025-12-31 дейін түсім $2,000,000.00-дан кем емес.
6.2-тармақ Күрделі шығындардың түсімге арақатынасы.
Күрделі шығындар / түсім 0.25х мөлшерінен аспауға тиіс.
6.3-тармақ Коммуналдық шығындардың ең жоғары сомасы.
Коммуналдық шығындар $400,000.00-дан артық емес.
7-бап. Қорытынды ережелер
"""

    assert _classify(text) == (DocKind.CREDIT_AGREEMENT, Edition.CURRENT)
    rules = extract_rules("K1", text)
    assert set(rules) == {"6.1", "6.2", "6.3"}
    assert rules["6.1"].kind is RuleKind.MIN_REVENUE
    assert rules["6.1"].comparator == ">="
    assert rules["6.1"].period[0].isoformat() == "2025-01-01"
    assert rules["6.2"].threshold == Decimal("0.25")
    assert rules["6.2"].categories == frozenset({Category.CAPEX, Category.REVENUE})
    assert rules["6.3"].categories == frozenset({Category.UTILITIES})


def test_kazakh_revenue_minimum_with_must_not_be_less_wording() -> None:
    text = """
БАНКТІК ҚАРЫЗ ШАРТЫ
6-бап. Қаржылық ковенанттар
6.2-тармақ Түсімнің ең төменгі деңгейі.
2025-01-01 бастап 2025-12-31 дейін түсім USD 1,000,000-нан кем болмауға тиіс.
7-бап. Қорытынды ережелер
"""

    rule = extract_rules("K2", text)["6.2"]

    assert rule.kind is RuleKind.MIN_REVENUE
    assert rule.comparator == ">="
    assert rule.threshold == Decimal("1000000")
    assert rule.categories == frozenset({Category.REVENUE})


def test_kazakh_ledger_descriptions_are_categorized() -> None:
    assert categorize("Жабдық сатып алу бойынша күрделі шығындар") is Category.CAPEX
    assert categorize("Қызметкерлерге еңбекақы төлеу") is Category.PERSONNEL
    assert categorize("Клиент төлемі қызметтен түскен кіріс", is_inflow=True) is Category.REVENUE
    assert categorize("Сақтандыру сыйлықақысын қайтару", is_inflow=True) is Category.CONTRA


def test_kazakh_kyc_threshold_and_legal_suffixes() -> None:
    parties = extract_related_parties(
        "K1",
        """
Клиентті таны / KYC
Байланысты тараптар және меншік құрылымы:
Алатау Холдинг ЖШС
31.5%
Тәуелсіз Сервис ЖШС
12.0%
Иеленетін үлесі 25.0% және одан жоғары ұйым байланысты тарап болып саналады.
""",
    )

    assert parties.threshold_percent == Decimal("25.0")
    assert parties.names == frozenset({"Алатау Холдинг ЖШС"})


def test_kazakh_audit_adjustments_are_transaction_scoped() -> None:
    adjustments = extract_adjustments(
        """
TXN-K1-0001 бастапқыда маркетингтік шығындар ретінде есепке алынған,
аудитор күрделі шығындар ретінде қайта жіктеді.
TXN-K1-0002 ковенанттық кезеңнен алынып тасталды.
"""
    )

    assert [(item.kind, item.txn_id, item.new_category) for item in adjustments] == [
        (AdjustmentKind.RECLASSIFY, "TXN-K1-0001", Category.CAPEX),
        (AdjustmentKind.EXCLUDE, "TXN-K1-0002", None),
    ]
