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
        assert len(result.json()["map_points"]) == 2
        assert result.json()["map_points"][0]["place_id"].startswith("place_")
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
        assert "optimizer.solve" in stream.text

        deleted = api.delete(f"/api/v1/runs/{run_id}", headers=headers)
        assert deleted.status_code == 204
        assert api.get(f"/api/v1/runs/{run_id}", headers=headers).status_code == 404


def test_targeted_replanning_versions_diff_restore_and_exports() -> None:
    with client() as api:
        run, headers = create_run(api, key="replan-request-key")
        run_id = run["run_id"]

        replanned = api.post(
            f"/api/v1/runs/{run_id}/replan",
            headers=headers,
            json={"instruction": "第二天轻松一点", "base_version": 1},
        )
        assert replanned.status_code == 200
        assert replanned.json()["version"] == 2
        relaxed = next(
            plan for plan in replanned.json()["bundle"]["plans"] if plan["strategy"] == "relaxed"
        )
        assert len(relaxed["days"][0]["items"]) == 1

        versions = api.get(f"/api/v1/runs/{run_id}/versions", headers=headers)
        assert [item["version"] for item in versions.json()["versions"]] == [1, 2]
        changed_tasks = versions.json()["versions"][1]["changed_task_ids"]
        assert "itinerary_planning" in changed_tasks
        assert "destination_intelligence" not in changed_tasks

        difference = api.get(f"/api/v1/runs/{run_id}/versions/diff?from=1&to=2", headers=headers)
        assert difference.status_code == 200
        assert difference.json()["changed_paths"]

        markdown = api.get(f"/api/v1/runs/{run_id}/export?format=markdown", headers=headers)
        exported_json = api.get(f"/api/v1/runs/{run_id}/export?format=json", headers=headers)
        assert markdown.headers["content-disposition"].endswith('.md"')
        assert markdown.text.startswith("# 北京")
        assert exported_json.json()["run_id"] == run_id

        restored = api.post(f"/api/v1/runs/{run_id}/versions/1/restore", headers=headers)
        assert restored.status_code == 200
        assert restored.json()["version"] == 1
        assert api.get(f"/api/v1/runs/{run_id}", headers=headers).json()["current_version"] == 1
