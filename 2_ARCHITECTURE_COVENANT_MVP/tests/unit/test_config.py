from pathlib import Path

from halyk_covenants.config import load_settings


def test_environment_overrides_yaml_configuration(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "storage:\n  duckdb_path: yaml.duckdb\n"
        "evaluation:\n  continue_on_error: false\n  store_calculation_sql: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HALYK_STORAGE__DUCKDB_PATH", "environment.duckdb")
    monkeypatch.setenv("HALYK_EVALUATION__CONTINUE_ON_ERROR", "true")

    settings = load_settings(config)

    assert settings.storage.duckdb_path == Path("environment.duckdb")
    assert settings.evaluation.continue_on_error is True
    assert settings.evaluation.store_calculation_sql is False
