"""Filesystem lifecycle and serialization for full pipeline traces."""

from __future__ import annotations

import csv
import json
import shutil
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

TRACE_OWNER = "halyk-fulltrace"
TRACE_FORMAT_VERSION = 1


def jsonable(value: Any) -> Any:
    """Convert pipeline objects to stable, precision-preserving JSON values."""
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return jsonable(model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [jsonable(item) for item in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return jsonable(value.value)
    if isinstance(value, Path):
        return str(value)
    return value


def is_unsafe_trace_root(root: Path) -> bool:
    """Reject roots whose recursive recreation could erase broad user data."""
    resolved = root.expanduser().resolve()
    current = Path.cwd().resolve()
    home = Path.home().resolve()
    protected = {current, home, Path(resolved.anchor), *current.parents, *home.parents}
    return resolved in protected


def _is_owned_trace(root: Path) -> bool:
    manifest = root / "manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        payload.get("owner") == TRACE_OWNER
        and payload.get("format_version") == TRACE_FORMAT_VERSION
        and payload.get("fulltrace") is True
        and isinstance(payload.get("stages"), list)
    )


class TraceWriter:
    """Own one recreated trace directory and its ordered manifest."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.active_stage: str | None = None
        self._manifest: dict[str, Any] = {
            "owner": TRACE_OWNER,
            "format_version": TRACE_FORMAT_VERSION,
            "fulltrace": True,
            "trace_root": str(root),
            "started_at": datetime.now().astimezone().isoformat(),
            "stages": [],
        }

    @classmethod
    def create(cls, root: Path) -> TraceWriter:
        expanded = root.expanduser()
        if expanded.is_symlink():
            raise ValueError(f"trace directory must not be a symlink: {expanded}")
        resolved = expanded.resolve()
        if is_unsafe_trace_root(resolved):
            raise ValueError(f"unsafe trace directory: {resolved}")
        if resolved.exists():
            if not resolved.is_dir():
                raise ValueError(f"trace path is not a directory: {resolved}")
            if any(resolved.iterdir()) and not _is_owned_trace(resolved):
                raise ValueError(f"trace directory is not owned by Halyk fulltrace: {resolved}")
            shutil.rmtree(resolved)
        resolved.mkdir(parents=True)
        writer = cls(resolved)
        writer._flush_manifest()
        return writer

    def _stage(self, name: str) -> dict[str, Any]:
        for stage in self._manifest["stages"]:
            if stage["name"] == name:
                return stage
        stage = {"name": name, "status": "in_progress", "artifacts": []}
        self._manifest["stages"].append(stage)
        return stage

    def _artifact_path(self, stage: str, name: str) -> Path:
        directory = (self.root / stage).resolve()
        if directory == self.root or not directory.is_relative_to(self.root):
            raise ValueError(f"unsafe trace stage: {stage}")
        path = (directory / name).resolve()
        if path == directory or not path.is_relative_to(directory):
            raise ValueError(f"unsafe trace artifact: {stage}/{name}")
        directory.mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _register(self, stage: str, path: Path) -> None:
        relative = path.relative_to(self.root).as_posix()
        record = self._stage(stage)
        if relative not in record["artifacts"]:
            record["artifacts"].append(relative)
        self._flush_manifest()

    def _flush_manifest(self) -> None:
        path = self.root / "manifest.json"
        path.write_text(
            json.dumps(jsonable(self._manifest), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def update_stage(self, stage: str, **metadata: Any) -> None:
        self._stage(stage).update(jsonable(metadata))
        self._flush_manifest()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        record = self._stage(name)
        record["status"] = "in_progress"
        record.pop("error", None)
        self.active_stage = name
        self._flush_manifest()
        try:
            yield
        except Exception as exc:
            self.fail_stage(name, exc)
            raise
        else:
            if record["status"] == "in_progress":
                record["status"] = "completed"
            self.active_stage = None
            self._flush_manifest()

    def fail_stage(self, stage: str, error: BaseException) -> None:
        self.update_stage(
            stage,
            status="failed",
            error={"type": type(error).__name__, "message": str(error)},
        )
        if self.active_stage == stage:
            self.active_stage = None

    def write_json(self, stage: str, name: str, payload: Any) -> Path:
        path = self._artifact_path(stage, name)
        path.write_text(
            json.dumps(jsonable(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._register(stage, path)
        return path

    def write_text(self, stage: str, name: str, content: str) -> Path:
        path = self._artifact_path(stage, name)
        path.write_text(content, encoding="utf-8")
        self._register(stage, path)
        return path

    def write_csv(
        self,
        stage: str,
        name: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        fieldnames: list[str] | None = None,
    ) -> Path:
        materialized = [jsonable(dict(row)) for row in rows]
        columns = fieldnames or (list(materialized[0]) if materialized else [])
        path = self._artifact_path(stage, name)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            if columns:
                writer.writeheader()
                writer.writerows(materialized)
        self._register(stage, path)
        return path
