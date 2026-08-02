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


class DeepSeekSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "deepseek-v4-pro"
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: float = 90.0
    max_retries: int = Field(default=2, ge=0, le=5)


class OCRSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    native_text_min_chars: int = Field(default=80, ge=0)
    device: str = "gpu:0"
    cpu_fallback: bool = True
    max_page_pixels: int = Field(default=12_000_000, gt=0)


class TracingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_mode: str = Field(default="redacted", pattern="^(redacted|full)$")
    store_local_stage_records: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HALYK_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    storage: StorageSettings = Field(default_factory=StorageSettings)
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)
    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)
    ocr: OCRSettings = Field(default_factory=OCRSettings)
    tracing: TracingSettings = Field(default_factory=TracingSettings)

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
