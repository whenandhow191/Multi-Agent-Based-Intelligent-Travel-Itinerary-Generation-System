"""Tests for typed configuration layers and secret-safe diagnostics."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from apps.api.config import render_doctor
from apps.api.settings import Settings, load_settings


def test_process_environment_overrides_profile_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Process variables should win over environment-specific dotenv files."""

    (tmp_path / ".env").write_text("API_PORT=8001\n", encoding="utf-8")
    (tmp_path / ".env.development").write_text("API_PORT=8002\n", encoding="utf-8")
    local_file = tmp_path / ".env.local"
    local_file.write_text("API_PORT=8003\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("API_PORT", "9100")

    assert load_settings(tmp_path).api_port == 9100

    monkeypatch.delenv("API_PORT")
    assert load_settings(tmp_path).api_port == 8003

    local_file.unlink()
    assert load_settings(tmp_path).api_port == 8002


def test_production_debug_mode_is_rejected() -> None:
    """Unsafe debugging must fail during settings validation."""

    with pytest.raises(ValidationError, match="DEBUG must be false"):
        Settings(app_env="production", debug=True)


def test_doctor_reports_presence_without_secret_values() -> None:
    """Doctor output must expose status but never credential contents."""

    secret = "sk-super-secret-value"
    settings = Settings(openai_api_key=SecretStr(secret))

    output = render_doctor(settings)

    assert "OPENAI_API_KEY: present (optional)" in output
    assert secret not in output
    assert secret not in repr(settings)
