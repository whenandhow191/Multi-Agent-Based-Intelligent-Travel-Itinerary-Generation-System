"""C39 Trip Run API coverage over the deterministic Fixture service."""

from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.run_routes import get_run_service
from apps.api.run_service import InMemoryTripRunService
from packages.evals.fixtures import build_trip_request


def client() -> TestClient:
    service = InMemoryTripRunService()
    app.dependency_overrides[get_run_service] = lambda: service
    return TestClient(app)


def test_openapi_exposes_complete_trip_run_fixture_flow() -> None:
    with client() as api:
        created = api.post(
            "/api/v1/runs",
            json={"request": build_trip_request().model_dump(mode="json")},
        )
        assert created.status_code == 201
        run = created.json()
        run_id = run["run_id"]
        assert run["fixture_mode"] is True
        assert run["state"] == "succeeded"

        fetched = api.get(f"/api/v1/runs/{run_id}")
        result = api.get(f"/api/v1/runs/{run_id}/result")
        comparison = api.get(f"/api/v1/runs/{run_id}/comparison")
        clarified = api.post(
            f"/api/v1/runs/{run_id}/clarifications",
            json={"question_id": "pace", "answer": "轻松一点"},
        )

        assert fetched.status_code == 200
        assert result.status_code == 200
        assert len(result.json()["bundle"]["plans"]) == 3
        assert len(comparison.json()["comparison"]["entries"]) == 3
        assert clarified.json()["clarification_answers"][0]["answer"] == "轻松一点"

        cancelled = api.post(f"/api/v1/runs/{run_id}/cancel")
        assert cancelled.json()["state"] == "cancelled"
        assert api.get(f"/api/v1/runs/{run_id}/result").status_code == 409

        schema = api.get("/openapi.json").json()
        assert "/api/v1/runs/{run_id}/result" in schema["paths"]
        assert "/api/v1/runs/{run_id}/comparison" in schema["paths"]


def test_fixture_runner_rejects_unsupported_destination() -> None:
    request = build_trip_request().model_copy(update={"destination": "广州"})
    with client() as api:
        response = api.post("/api/v1/runs", json={"request": request.model_dump(mode="json")})
    assert response.status_code == 409
