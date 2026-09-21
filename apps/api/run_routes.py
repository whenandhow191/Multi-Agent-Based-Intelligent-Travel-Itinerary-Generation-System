"""FastAPI routes for secure trip-run lifecycle and progress streaming."""

from collections.abc import AsyncIterator
from json import dumps
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from apps.api.run_models import (
    ClarificationAnswer,
    PlanComparisonResponse,
    ReplanRequest,
    RunVersionDiff,
    RunVersionList,
    TripRunCreated,
    TripRunCreateRequest,
    TripRunResource,
    TripRunResultResponse,
)
from apps.api.run_service import (
    InMemoryTripRunService,
    RunAccessError,
    RunConflictError,
    RunNotFoundError,
)

router = APIRouter(prefix="/api/v1/runs", tags=["trip runs"])
_service = InMemoryTripRunService()


def get_run_service() -> InMemoryTripRunService:
    return _service


RunService = Annotated[InMemoryTripRunService, Depends(get_run_service)]
AccessToken = Annotated[str, Header(alias="X-Run-Token", min_length=32, max_length=128)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=120)]


def _not_found(error: RunNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "run_not_found", "message": f"run not found: {error.args[0]}"},
    )


def _access_denied(error: RunAccessError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "run_not_found", "message": "run not found"},
    )


def _conflict(error: RunConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "run_conflict", "message": str(error)},
    )


@router.post("", response_model=TripRunCreated, status_code=status.HTTP_201_CREATED)
async def create_trip_run(
    payload: TripRunCreateRequest,
    service: RunService,
    idempotency_key: IdempotencyKey,
) -> TripRunCreated:
    try:
        return service.create(payload.request, idempotency_key)
    except RunConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/{run_id}", response_model=TripRunResource)
async def get_trip_run(
    run_id: str, service: RunService, access_token: AccessToken
) -> TripRunResource:
    try:
        return service.get(run_id, access_token)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc


@router.post("/{run_id}/cancel", response_model=TripRunResource)
async def cancel_trip_run(
    run_id: str, service: RunService, access_token: AccessToken
) -> TripRunResource:
    try:
        return service.cancel(run_id, access_token)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc


@router.post("/{run_id}/clarifications", response_model=TripRunResource)
async def answer_clarification(
    run_id: str,
    payload: ClarificationAnswer,
    service: RunService,
    access_token: AccessToken,
) -> TripRunResource:
    try:
        return service.clarify(run_id, access_token, payload)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc
    except RunConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/{run_id}/result", response_model=TripRunResultResponse)
async def get_trip_run_result(
    run_id: str, service: RunService, access_token: AccessToken
) -> TripRunResultResponse:
    try:
        return service.result(run_id, access_token)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc
    except RunConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/{run_id}/comparison", response_model=PlanComparisonResponse)
async def compare_trip_run_plans(
    run_id: str, service: RunService, access_token: AccessToken
) -> PlanComparisonResponse:
    try:
        return service.comparison(run_id, access_token)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc
    except RunConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/{run_id}/events", response_class=StreamingResponse)
async def stream_trip_run_events(
    run_id: str, service: RunService, access_token: AccessToken
) -> StreamingResponse:
    try:
        events = service.events(run_id, access_token)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc

    async def generate() -> AsyncIterator[str]:
        for event in events:
            data = dumps(event.model_dump(mode="json"), ensure_ascii=False)
            yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip_run(run_id: str, service: RunService, access_token: AccessToken) -> Response:
    try:
        service.delete(run_id, access_token)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{run_id}/replan", response_model=TripRunResultResponse)
async def replan_trip_run(
    run_id: str,
    payload: ReplanRequest,
    service: RunService,
    access_token: AccessToken,
) -> TripRunResultResponse:
    try:
        return service.replan(run_id, access_token, payload)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc
    except RunConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/{run_id}/versions", response_model=RunVersionList)
async def list_trip_run_versions(
    run_id: str, service: RunService, access_token: AccessToken
) -> RunVersionList:
    try:
        return service.versions(run_id, access_token)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc


@router.get("/{run_id}/versions/diff", response_model=RunVersionDiff)
async def diff_trip_run_versions(
    run_id: str,
    service: RunService,
    access_token: AccessToken,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
) -> RunVersionDiff:
    try:
        return service.diff(run_id, access_token, from_version, to_version)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc


@router.post("/{run_id}/versions/{version}/restore", response_model=TripRunResultResponse)
async def restore_trip_run_version(
    run_id: str,
    version: int,
    service: RunService,
    access_token: AccessToken,
) -> TripRunResultResponse:
    try:
        return service.restore(run_id, access_token, version)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc


@router.get("/{run_id}/export")
async def export_trip_run(
    run_id: str,
    service: RunService,
    access_token: AccessToken,
    format_name: Annotated[str, Query(alias="format", pattern="^(markdown|json)$")] = "markdown",
) -> Response:
    try:
        content, media_type = service.export(run_id, access_token, format_name)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunAccessError as exc:
        raise _access_denied(exc) from exc
    except RunConflictError as exc:
        raise _conflict(exc) from exc
    extension = "md" if format_name == "markdown" else "json"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{run_id}.{extension}"'},
    )
