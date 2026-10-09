from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Digital Transformer Twin API"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    database_url: SecretStr | None = None
    debug: bool = False
    # Relative paths resolve against the project root.
    thresholds_file: Path = Path("config") / "thresholds.yaml"

    @field_validator("database_url")
    @classmethod
    def require_postgresql(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                valid = make_url(value.get_secret_value()).drivername == "postgresql+psycopg"
            except Exception:
                valid = False
            if not valid:
                raise ValueError("DATABASE_URL must use postgresql+psycopg")
        return value

    @field_validator("thresholds_file")
    @classmethod
    def project_relative(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("api_v1_prefix")
    @classmethod
    def valid_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("API_V1_PREFIX must start with / and have no trailing /")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
