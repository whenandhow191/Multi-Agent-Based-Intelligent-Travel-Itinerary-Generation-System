"""Credential-free release demonstration using the deterministic Fixture path."""

from typing import Annotated

from pydantic import Field

from apps.api.run_service import InMemoryTripRunService
from packages.domain.common import DomainModel, Identifier
from packages.evals.fixtures import build_trip_request


class DemoSummary(DomainModel):
    release: str
    run_id: Identifier
    state: str
    destination: str
    plan_count: Annotated[int, Field(ge=1)]
    artifact_count: Annotated[int, Field(ge=1)]
    event_count: Annotated[int, Field(ge=1)]
    fixture_warning: str


def run_demo() -> DemoSummary:
    """Execute create/read/result/events without network or Provider credentials."""

    service = InMemoryTripRunService()
    created = service.create(build_trip_request(), "release-demo-v1")
    run_id = created.run.run_id
    token = created.access_token
    result = service.result(run_id, token)
    events = service.events(run_id, token)
    artifact_ids = {artifact_id for event in events for artifact_id in event.artifact_ids}
    return DemoSummary(
        release="1.0.0",
        run_id=run_id,
        state=created.run.state,
        destination=created.run.request.destination,
        plan_count=len(result.bundle.plans),
        artifact_count=len(artifact_ids),
        event_count=len(events),
        fixture_warning="合成 Fixture，仅用于验收，不代表实时价格或库存。",
    )


def main() -> int:
    print(run_demo().model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
