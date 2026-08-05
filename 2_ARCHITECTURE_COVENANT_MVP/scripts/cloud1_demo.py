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
from types import SimpleNamespace

from halyk_covenants.covenants import CovenantRegistry
from halyk_covenants.domain import CovenantSpec
from halyk_covenants.evaluators import EvaluationService, TemporalEvaluationService
from halyk_covenants.pipeline import BatchEvaluationPipeline
from halyk_covenants.review.spec_models import ContextGrade, SpecReviewDecision
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
    """Stands in for the LLM reviewer and context grader so the demo runs offline.

    Reproduces both causes of a wrong specification:
      A. the compiler misread context it had        -> recompile can fix it
      B. the context never contained the answer     -> needs a targeted re-search
    """

    model_name = "scripted-demo"
    prompt_version = "demo-v2"

    def __init__(self, misread: set[str], missing_context: set[str]) -> None:
        self.misread = misread
        self.missing_context = missing_context
        self.seen: list[str] = []
        self.graded: list[str] = []
        self.rejected_once: set[str] = set()

    def review_spec(self, spec: CovenantSpec, context: str = "") -> SpecReviewDecision:
        self.seen.append(spec.covenant_id)
        first_pass = spec.covenant_id not in self.rejected_once

        if spec.covenant_id in self.misread and first_pass:
            self.rejected_once.add(spec.covenant_id)
            return SpecReviewDecision(
                accepted=False,
                confidence=0.35,
                objection=(
                    "Пункт говорит о КОЛИЧЕСТВЕ операций, "
                    "а спецификация считает СУММУ (metric_type=sum)."
                ),
                issues=["metric_type mismatch"],
            )

        if spec.covenant_id in self.missing_context and first_pass:
            self.rejected_once.add(spec.covenant_id)
            return SpecReviewDecision(
                accepted=False,
                confidence=0.30,
                objection="В строке таблицы не указана валюта, а в контексте нет её определения.",
                issues=["currency undetermined"],
            )

        return SpecReviewDecision(accepted=True, confidence=0.93)

    def grade_context(self, spec: CovenantSpec, context: str, objection: str) -> ContextGrade:
        self.graded.append(spec.covenant_id)
        if spec.covenant_id in self.missing_context:
            return ContextGrade(
                sufficient=False,
                missing_query="пустая валюта означает KZT вводное определение",
                confidence=0.82,
                reasoning="сноска, определяющая пустую ячейку валюты, не попала в контекст",
            )
        return ContextGrade(
            sufficient=True,
            confidence=0.88,
            reasoning="текст пункта присутствует целиком, ошибка в прочтении",
        )


class DemoExpander:
    """Returns the footnote that the original retrieval missed."""

    FOOTNOTE = (
        "[document=borrower_limits_appendix.pdf page=1 type=text] "
        "* Пустая валюта в строке MAX означает KZT согласно вводному определению на этой странице."
    )

    def __init__(self) -> None:
        self.queries: list[str] = []

    def expand(self, query: str, candidate: object, current_context: str) -> str:
        self.queries.append(query)
        return self.FOOTNOTE


class ReplayCompiler:
    """Stands in for CompilerGraph: returns the candidate spec unchanged.

    The golden specs are already correct, so recompilation is a no-op here — the
    demo is about which path the graph takes, not about the compiler's output.
    """

    def __init__(self) -> None:
        self.contexts: list[str] = []

    def invoke(self, state: dict) -> dict:
        self.contexts.append(state.get("context", ""))
        outcome = SimpleNamespace(specs=[state["candidate"]])
        return {"status": "compiled", "outcome": outcome, "validation_errors": []}


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

        # --- 2. spec review graph, the Cloud1 move --------------------------------
        head("ШАГ 2 — Граф ревью спецификаций (LangGraph)")
        print(f"{DIM}review -> grade_context -> expand_retrieval -> recompile -> review")
        print("Ревьюер видит текст пункта, спецификацию и контекст документа.")
        print(f"Чисел ещё нет — вычисление не запускалось.{RESET}\n")

        reviewer = ScriptedReviewer(
            misread={"COV-ALPHA-COUNT"},  # причина A: неверно прочитал
            missing_context={"COV-BETA-MAX"},  # причина B: не хватило контекста
        )
        expander = DemoExpander()
        compiler = ReplayCompiler()
        service = SpecReviewService(reviewer=reviewer, compiler_graph=compiler, expander=expander)

        reviewed: list[CovenantSpec] = []
        for spec in specs:
            result = service.review_and_maybe_recompile(
                spec, candidate=spec, document_context=f"CLAUSE: {spec.raw_text}"
            )
            reviewed.append(result.spec)
            registry.save(result.spec)

            trust = result.spec.spec_trust
            colour = {"accepted": GREEN, "revised": YELLOW, "low": RED}[trust]
            print(f"  {colour}{trust:<9}{RESET} {spec.covenant_id}")
            if result.spec.review_objection:
                print(f"            {DIM}-> {result.spec.review_objection}{RESET}")
            if result.context_grade is not None:
                grade = result.context_grade
                verdict = "хватало" if grade.sufficient else "НЕ хватало"
                print(
                    f"            {DIM}   грейдинг контекста: {verdict} — {grade.reasoning}{RESET}"
                )
            if result.context_expanded:
                print(f"            {YELLOW}   повторный поиск: '{expander.queries[-1]}'{RESET}")

        print(
            f"\n{DIM}Причина A ({BOLD}COV-ALPHA-COUNT{RESET}{DIM}): контекст был полным, "
            f"модель ошиблась в прочтении."
        )
        print(f"  -> перекомпиляция с тем же контекстом{RESET}")
        print(
            f"{DIM}Причина B ({BOLD}COV-BETA-MAX{RESET}{DIM}): валюта определена сноской, "
            f"которой не было в контексте."
        )
        print(f"  -> перекомпиляция с тем же контекстом БЕССМЫСЛЕННА, нужен повторный поиск{RESET}")

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
