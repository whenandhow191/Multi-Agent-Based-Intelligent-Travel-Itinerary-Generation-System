"""C39-C40 Trip Run API, security, progress and lifecycle coverage."""

from typing import Any

from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.run_routes import get_run_service
from apps.api.run_service import InMemoryTripRunService
from packages.evals.fixtures import build_trip_request


def client() -> TestClient:
    service = InMemoryTripRunService()
    app.dependency_overrides[get_run_service] = lambda: service
    return TestClient(app)


def create_run(
    api: TestClient, *, key: str = "fixture-request-001"
) -> tuple[dict[str, Any], dict[str, str]]:
    response = api.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": key},
        json={"request": build_trip_request().model_dump(mode="json")},
    )
    assert response.status_code == 201
    created = response.json()
    return created["run"], {"X-Run-Token": created["access_token"]}


def test_openapi_exposes_complete_trip_run_fixture_flow() -> None:
    with client() as api:
        run, headers = create_run(api)
        run_id = run["run_id"]
        assert run["fixture_mode"] is True
        assert run["state"] == "succeeded"

        fetched = api.get(f"/api/v1/runs/{run_id}", headers=headers)
        result = api.get(f"/api/v1/runs/{run_id}/result", headers=headers)
        comparison = api.get(f"/api/v1/runs/{run_id}/comparison", headers=headers)
        clarified = api.post(
            f"/api/v1/runs/{run_id}/clarifications",
            headers=headers,
            json={"question_id": "pace", "answer": "轻松一点"},
        )

        assert fetched.status_code == 200
        assert result.status_code == 200
        assert len(result.json()["bundle"]["plans"]) == 3
        assert len(comparison.json()["comparison"]["entries"]) == 3
        assert clarified.json()["clarification_answers"][0]["answer"] == "轻松一点"

        cancelled = api.post(f"/api/v1/runs/{run_id}/cancel", headers=headers)
        assert cancelled.json()["state"] == "cancelled"
        assert api.get(f"/api/v1/runs/{run_id}/result", headers=headers).status_code == 409

        schema = api.get("/openapi.json").json()
        assert "/api/v1/runs/{run_id}/result" in schema["paths"]
        assert "/api/v1/runs/{run_id}/comparison" in schema["paths"]


def test_fixture_runner_rejects_unsupported_destination() -> None:
    request = build_trip_request().model_copy(update={"destination": "广州"})
    with client() as api:
        response = api.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "unsupported-destination"},
            json={"request": request.model_dump(mode="json")},
        )
    assert response.status_code == 409


def test_token_sse_idempotency_and_delete_lifecycle() -> None:
    with client() as api:
        run, headers = create_run(api, key="same-request-key")
        run_id = run["run_id"]
        repeated, repeated_headers = create_run(api, key="same-request-key")

        assert repeated["run_id"] == run_id
        assert repeated_headers == headers
        assert api.get(f"/api/v1/runs/{run_id}").status_code == 422
        assert (
            api.get(f"/api/v1/runs/{run_id}", headers={"X-Run-Token": "0" * 64}).status_code == 404
        )

        stream = api.get(f"/api/v1/runs/{run_id}/events", headers=headers)
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: run.started" in stream.text
        assert "event: run.completed" in stream.text
        assert "artifact_candidates_fixture" in stream.text

        deleted = api.delete(f"/api/v1/runs/{run_id}", headers=headers)
        assert deleted.status_code == 204
        assert api.get(f"/api/v1/runs/{run_id}", headers=headers).status_code == 404
