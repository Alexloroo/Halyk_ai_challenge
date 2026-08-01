from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duckdb_path: Path = Path("data/duckdb/hackathon.duckdb")


class EvaluationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    continue_on_error: bool = True
    store_calculation_sql: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HALYK_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    storage: StorageSettings = Field(default_factory=StorageSettings)
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del cls, settings_cls
        return env_settings, init_settings, dotenv_settings, file_secret_settings


def load_settings(config_path: Path | None = None) -> Settings:
    """Load YAML defaults, then apply HALYK_* environment overrides."""
    values: dict[str, object] = {}
    if config_path is not None:
        with config_path.open(encoding="utf-8") as stream:
            values = yaml.safe_load(stream) or {}
    return Settings(**values)
