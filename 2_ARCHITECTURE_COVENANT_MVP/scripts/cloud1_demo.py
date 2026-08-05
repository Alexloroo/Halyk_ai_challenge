"""End-to-end Cloud1 demo on mock data. No API key, no network, no Docker.

Runs the whole pipeline against the synthetic dataset and compares every answer
with the golden expectation, then prints what Cloud1 adds on top of codex-1:
the spec reviewer, the expectation manifest and the triage-ranked confidence report.

    python scripts/cloud1_demo.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from halyk_covenants.covenants import CovenantRegistry
from halyk_covenants.domain import CovenantSpec
from halyk_covenants.evaluators import EvaluationService, TemporalEvaluationService
from halyk_covenants.pipeline import BatchEvaluationPipeline
from halyk_covenants.review.spec_models import SpecReviewDecision
from halyk_covenants.review.spec_review_service import SpecReviewService
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.verification import ManifestBuilder, build_confidence_report

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "synthetic"
OUT = ROOT / "data" / "demo"

# Windows consoles default to a legacy codepage that cannot encode Cyrillic.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)


def head(title: str) -> None:
    print(f"\n{BOLD}{'=' * 74}\n{title}\n{'=' * 74}{RESET}")


class ScriptedReviewer:
    """Stands in for the LLM reviewer so the demo runs offline.

    Rejects exactly one covenant to make the recompilation path visible.
    """

    model_name = "scripted-demo"
    prompt_version = "demo-v1"

    def __init__(self, reject: set[str]) -> None:
        self.reject = reject
        self.seen: list[str] = []

    def review_spec(self, spec: CovenantSpec) -> SpecReviewDecision:
        self.seen.append(spec.covenant_id)
        if spec.covenant_id in self.reject:
            return SpecReviewDecision(
                accepted=False,
                confidence=0.35,
                objection=(
                    "Пункт говорит о КОЛИЧЕСТВЕ операций, "
                    "а спецификация считает СУММУ (metric_type=sum)."
                ),
                issues=["metric_type mismatch"],
            )
        return SpecReviewDecision(accepted=True, confidence=0.93)


def load_dataset(store: DuckDBStore) -> tuple[list[CovenantSpec], list[dict]]:
    transactions = next((DATASET / "transactions").glob("*.xlsx"))
    rows = store.load_transactions(transactions)
    print(f"  транзакций загружено: {BOLD}{rows}{RESET}  ({transactions.name})")

    specs = [
        CovenantSpec.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((DATASET / "covenants").glob("*.json"))
    ]
    print(f"  ковенантов загружено: {BOLD}{len(specs)}{RESET}")

    golden = [
        json.loads(line)
        for line in (DATASET / "benchmark" / "qa_pairs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    print(f"  эталонных вопросов:   {BOLD}{len(golden)}{RESET}")
    return specs, golden


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    db = OUT / "demo.duckdb"
    db.unlink(missing_ok=True)

    head("ШАГ 1 — Загрузка моковых данных")
    with DuckDBStore(db) as store:
        specs, golden = load_dataset(store)
        registry = CovenantRegistry(store)

        # --- 2. spec review, the Cloud1 move --------------------------------------
        head("ШАГ 2 — Ревью спецификаций (главное отличие Cloud1)")
        print(f"{DIM}Ревьюер видит текст пункта и спецификацию. Чисел ещё нет —")
        print(f"вычисление не запускалось. Изменить ответ он физически не может.{RESET}\n")

        reviewer = ScriptedReviewer(reject={"COV-ALPHA-COUNT"})
        service = SpecReviewService(reviewer=reviewer, compiler_graph=None)

        reviewed: list[CovenantSpec] = []
        for spec in specs:
            result = service.review_and_maybe_recompile(spec)
            reviewed.append(result.spec)
            registry.save(result.spec)

            trust = result.spec.spec_trust
            colour = {"accepted": GREEN, "revised": YELLOW, "low": RED}[trust]
            print(f"  {colour}{trust:<9}{RESET} {spec.covenant_id}")
            if result.spec.review_objection:
                print(f"            {DIM}-> {result.spec.review_objection}{RESET}")

        # --- 3. expectation manifest ----------------------------------------------
        head("ШАГ 3 — Манифест ожиданий (независимая проверка полноты)")
        questions = {
            (case["borrower_id"], case["covenant_id"]): case["question"] for case in golden
        }
        # A question nobody can answer: the covenant was never detected.
        questions[("B001", "COV-NOT-DETECTED")] = "Ковенант, который детектор пропустил"

        manifest = ManifestBuilder(store, registry).build(questions)
        print(f"  вопросов организатора: {BOLD}{len(questions)}{RESET}")
        print(f"  пар в манифесте:       {BOLD}{len(manifest.expected_pairs)}{RESET}")
        print(f"\n{DIM}В манифест намеренно добавлена пара COV-NOT-DETECTED, которой")
        print(f"нет ни в одном документе — чтобы показать, что пропуск теперь виден.{RESET}")

        # --- 4. evaluation ---------------------------------------------------------
        head("ШАГ 4 — Детерминированное вычисление (вызовов LLM: 0)")
        report = BatchEvaluationPipeline(store, registry, manifest=manifest).run(date(2026, 4, 30))
        print(f"  ожидалось пар: {BOLD}{report.expected_pair_count}{RESET}")
        print(f"  получено пар:  {BOLD}{report.actual_pair_count}{RESET}")

        missing = [i for i in report.verification.issues if i.code == "missing_result"]
        print(
            f"\n  {GREEN if missing else RED}Проверка полноты сработала: "
            f"{len(missing)} пропуск(ов) найдено{RESET}"
        )
        for issue in missing:
            print(
                f"    {RED}FAIL{RESET} {issue.borrower_id} / {issue.covenant_id} — {issue.message}"
            )
        print(f"{DIM}  В codex-1 и codex-2 эта проверка не могла сработать никогда:")
        print(f"  список ожидаемого строился из самих результатов.{RESET}")

        # --- 5. compare with golden answers ---------------------------------------
        # Every golden case carries its own evaluation date, so each is evaluated
        # at that date rather than at the single batch date used above.
        head("ШАГ 5 — Сверка с эталонными ответами")
        service = EvaluationService(store)
        temporal = TemporalEvaluationService(service)
        by_covenant: dict[str, list[CovenantSpec]] = {}
        for spec in reviewed:
            by_covenant.setdefault(spec.covenant_group_id or spec.covenant_id, []).append(spec)

        ok = bad = 0
        for case in golden:
            expected = case["answer"]
            at = date.fromisoformat(case["evaluation_date"])
            versions = by_covenant.get(case["covenant_id"])
            if not versions:
                print(f"  {RED}ОТСУТСТВУЕТ{RESET} {case['case_id']}")
                bad += 1
                continue

            result = temporal.evaluate_versions(versions, case["borrower_id"], at)

            verdict_ok = result.verdict == expected["verdict"]
            if expected["number"] is None:
                number_ok = result.number is None
            else:
                number_ok = result.number is not None and abs(
                    Decimal(str(result.number)) - Decimal(expected["number"])
                ) < Decimal("0.01")

            if verdict_ok and number_ok:
                print(
                    f"  {GREEN}OK  {RESET} {case['case_id']:<22} {case['evaluation_date']}  "
                    f"{result.verdict:<9} {result.number}"
                )
                ok += 1
            else:
                print(
                    f"  {RED}FAIL{RESET} {case['case_id']:<22} {case['evaluation_date']}  "
                    f"получено {result.verdict}/{result.number}, "
                    f"ожидалось {expected['verdict']}/{expected['number']}"
                )
                bad += 1

        # --- 6. confidence report --------------------------------------------------
        head("ШАГ 6 — Отчёт уверенности (что читать человеку)")
        spec_index = {s.covenant_id: s for s in reviewed}
        flags: dict[tuple[str, str], set[str]] = {}
        for issue in report.verification.issues:
            if issue.borrower_id and issue.covenant_id:
                flags.setdefault((issue.borrower_id, issue.covenant_id), set()).add(issue.code)

        entries = build_confidence_report(report.results, spec_index, flags)
        path = OUT / "confidence-report.json"
        path.write_text(
            json.dumps([e.model_dump(mode="json") for e in entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"{DIM}Отсортировано: самое сомнительное сверху. Разбирать с ранга 1.{RESET}\n")
        print(f"  {'ранг':<5} {'уровень':<11} {'доверие':<9} пара")
        print(f"  {'-' * 5} {'-' * 11} {'-' * 9} {'-' * 30}")
        for entry in entries[:6]:
            colour = {
                "unreliable": RED,
                "low": RED,
                "medium": YELLOW,
                "high": GREEN,
            }[entry.level]
            print(
                f"  {entry.triage_rank:<5} {colour}{entry.level:<11}{RESET} "
                f"{entry.spec_trust:<9} {entry.borrower_id}/{entry.covenant_id}"
            )

        # --- summary ---------------------------------------------------------------
        head("ИТОГ")
        print(
            f"  сверка с эталоном:      {GREEN if not bad else RED}"
            f"{ok} верно / {bad} неверно{RESET}"
        )
        print(
            f"  ревью спецификаций:     {len(reviewer.seen)} проверено, "
            f"{sum(1 for s in reviewed if s.spec_trust != 'accepted')} помечено"
        )
        print(f"  пропуски найдены:       {len(missing)}")
        print(f"  вызовов LLM на оценке:  {GREEN}0{RESET}")
        print(f"\n  отчёт: {path}")

    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
