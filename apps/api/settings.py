"""Typed, layered application configuration with secret-safe representations."""

from functools import lru_cache
from os import environ
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """Validated runtime settings loaded from files and process environment."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Environment = "development"
    debug: bool = False
    mock_mode: bool = True

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    web_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/travel_agent"
    )
    openai_api_key: SecretStr | None = None
    amap_web_service_key: SecretStr | None = None
    amap_js_key: SecretStr | None = None
    qweather_api_key: SecretStr | None = None

    @property
    def cors_origins(self) -> list[str]:
        """Return normalized browser origins without accepting wildcard credentials."""

        return [origin.strip().rstrip("/") for origin in self.web_origins.split(",") if origin]

    @model_validator(mode="after")
    def enforce_safe_profile(self) -> Self:
        """Reject development-only debugging in production."""

        if self.app_env == "production" and self.debug:
            raise ValueError("DEBUG must be false in production")
        return self


def configuration_files(project_root: Path, environment: Environment) -> tuple[Path, ...]:
    """Return configuration layers from lowest to highest file priority."""

    candidates = (
        project_root / ".env",
        project_root / f".env.{environment}",
        project_root / ".env.local",
    )
    return tuple(path for path in candidates if path.is_file())


def load_settings(project_root: Path | None = None) -> Settings:
    """Load defaults, layered dotenv files, then process environment overrides."""

    root = project_root or Path.cwd()
    environment_value = environ.get("APP_ENV", "development").lower()
    if environment_value not in {"development", "test", "production"}:
        return Settings()
    environment = cast(Environment, environment_value)
    env_files = configuration_files(root, environment)
    # ``_env_file`` is a documented pydantic-settings runtime argument that its
    # generated mypy signature cannot currently express.
    return Settings(_env_file=env_files or None)  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one validated settings object for the application process."""

    return load_settings()
