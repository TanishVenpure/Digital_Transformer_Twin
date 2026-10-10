from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    agent_credentials: str = ""
    # Chat explanations. "none" keeps answers deterministic; "gemini" sends the question,
    # risk assessment and maintenance evidence to Google Gemini (explicit opt-in).
    llm_provider: Literal["none", "gemini"] = "none"
    gemini_api_key: SecretStr | None = None  # env GEMINI_API_KEY; never logged
    gemini_model: str = "gemini-2.5-flash"
    # Also sent as the server deadline (X-Server-Timeout); Google returns 504
    # DEADLINE_EXCEEDED when generation exceeds it. 30 s: successful live calls took
    # about 10-11 s, while two calls hit a 20 s deadline. One attempt, no retries.
    gemini_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    # Thinking effort for Gemini 3.x models; "low" is Google's latency recommendation
    # for retrieval-style tasks. "default" sends nothing and uses the model default.
    gemini_thinking_level: Literal["default", "low", "medium", "high"] = "low"

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

    @field_validator("api_v1_prefix")
    @classmethod
    def valid_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("API_V1_PREFIX must start with / and have no trailing /")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
