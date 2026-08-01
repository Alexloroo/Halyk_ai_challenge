import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from halyk_covenants.synthetic.definitions import build_synthetic_definition
from halyk_covenants.synthetic.models import (
    ArtifactEntry,
    DatasetManifest,
    SyntheticDatasetDefinition,
)
from halyk_covenants.synthetic.pdf import render_pdfs
from halyk_covenants.synthetic.qa import write_qa_artifacts
from halyk_covenants.synthetic.validation import require_valid_dataset
from halyk_covenants.synthetic.workbook import render_workbook


def generate_synthetic_dataset(
    output_dir: Path,
    *,
    definition: SyntheticDatasetDefinition | None = None,
) -> DatasetManifest:
    definition = definition or build_synthetic_definition()
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-staging-", dir=output_dir.parent))
    try:
        _build_staged_dataset(definition, staging)
        manifest = _create_manifest(definition, staging)
        _write_json(staging / "manifest.json", manifest.model_dump(mode="json"))
        require_valid_dataset(staging)
        _replace_target(staging, output_dir)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _build_staged_dataset(definition: SyntheticDatasetDefinition, staging: Path) -> None:
    documents_dir = staging / "documents"
    transactions_dir = staging / "transactions"
    covenants_dir = staging / "covenants"
    benchmark_dir = staging / "benchmark"
    for directory in (documents_dir, transactions_dir, covenants_dir, benchmark_dir):
        directory.mkdir(parents=True, exist_ok=True)

    render_pdfs(definition, documents_dir)
    render_workbook(definition, transactions_dir / "synthetic_transactions.xlsx")
    for covenant in sorted(definition.covenants, key=lambda item: item.covenant_id):
        _write_json(
            covenants_dir / f"{covenant.covenant_id}.json",
            covenant.model_dump(mode="json"),
        )
    _write_json(
        benchmark_dir / "cases.json",
        [case.model_dump(mode="json") for case in definition.cases],
    )
    write_qa_artifacts(definition.cases, benchmark_dir)


def _create_manifest(
    definition: SyntheticDatasetDefinition,
    staging: Path,
) -> DatasetManifest:
    artifacts = []
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        relative = path.relative_to(staging).as_posix()
        artifacts.append(
            ArtifactEntry(
                path=relative,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                size_bytes=path.stat().st_size,
            )
        )
    return DatasetManifest(
        dataset_version=definition.dataset_version,
        artifacts=artifacts,
        document_defects={
            document.file_name: document.defects for document in definition.documents
        },
        known_limitations=[
            "PDF extraction, OCR, borrower discovery, and covenant compilation are not scored.",
            "TRIGGER_TRANSACTION evidence selection is outside Phase 1–3 and is expected "
            "to miss one evidence component.",
            "The exact duplicate is intentionally retained and contributes to aggregate metrics.",
        ],
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        f"{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )


def _replace_target(staging: Path, target: Path) -> None:
    if not target.exists():
        staging.rename(target)
        return

    backup = target.with_name(f".{target.name}-backup-{uuid4().hex}")
    target.rename(backup)
    try:
        staging.rename(target)
    except Exception:
        backup.rename(target)
        raise
    else:
        shutil.rmtree(backup)
