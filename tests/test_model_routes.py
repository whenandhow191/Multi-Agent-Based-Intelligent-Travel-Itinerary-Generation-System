"""API coverage for safe Penguin model discovery without browser-visible keys."""

from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.settings import get_settings


def test_model_catalog_is_safe_when_penguin_key_is_not_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("PENGUIN_API_KEY", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as api:
        response = api.get("/api/v1/models")
    get_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_id"] == "penguin"
    assert payload["configured"] is False
    assert payload["connection"] == "unconfigured"
    assert {item["id"] for item in payload["models"]} == {
        "claude-sonnet-5",
        "claude-opus-5",
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    }
