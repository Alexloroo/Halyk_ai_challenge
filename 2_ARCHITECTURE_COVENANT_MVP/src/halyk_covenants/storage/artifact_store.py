import json
from pathlib import Path
from uuid import uuid4

from halyk_covenants.observability import trace_stage


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.embedding_root = root / "embeddings"
        self.embedding_root.mkdir(parents=True, exist_ok=True)

    @trace_stage("retrieval.cache.get", run_type="tool")
    def get_embedding(self, key: str) -> list[float] | None:
        path = self.embedding_root / f"{key}.json"
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [float(value) for value in payload]

    @trace_stage("retrieval.cache.put", run_type="tool")
    def put_embedding(self, key: str, vector: list[float]) -> None:
        target = self.embedding_root / f"{key}.json"
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        temporary.write_text(f"{json.dumps(vector, separators=(',', ':'))}\n", encoding="utf-8")
        temporary.replace(target)
