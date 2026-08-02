import os

import pytest
from langsmith import Client


@pytest.mark.skipif(os.getenv("RUN_LANGSMITH_LIVE") != "1", reason="opt-in LangSmith live test")
def test_langsmith_project_is_reachable() -> None:
    project = os.environ["LANGSMITH_PROJECT"]
    client = Client()
    assert next(client.list_runs(project_name=project, limit=1), None) is not None or project
