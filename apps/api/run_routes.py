"""FastAPI routes for the trip-run product surface."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.run_models import (
    ClarificationAnswer,
    PlanComparisonResponse,
    TripRunCreateRequest,
    TripRunResource,
    TripRunResultResponse,
)
from apps.api.run_service import InMemoryTripRunService, RunConflictError, RunNotFoundError

router = APIRouter(prefix="/api/v1/runs", tags=["trip runs"])
_service = InMemoryTripRunService()


def get_run_service() -> InMemoryTripRunService:
    return _service


RunService = Annotated[InMemoryTripRunService, Depends(get_run_service)]


def _not_found(error: RunNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"run not found: {error.args[0]}"
    )


def _conflict(error: RunConflictError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


@router.post("", response_model=TripRunResource, status_code=status.HTTP_201_CREATED)
async def create_trip_run(payload: TripRunCreateRequest, service: RunService) -> TripRunResource:
    try:
        return service.create(payload.request)
    except RunConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/{run_id}", response_model=TripRunResource)
async def get_trip_run(run_id: str, service: RunService) -> TripRunResource:
    try:
        return service.get(run_id)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("/{run_id}/cancel", response_model=TripRunResource)
async def cancel_trip_run(run_id: str, service: RunService) -> TripRunResource:
    try:
        return service.cancel(run_id)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("/{run_id}/clarifications", response_model=TripRunResource)
async def answer_clarification(
    run_id: str, payload: ClarificationAnswer, service: RunService
) -> TripRunResource:
    try:
        return service.clarify(run_id, payload)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/{run_id}/result", response_model=TripRunResultResponse)
async def get_trip_run_result(run_id: str, service: RunService) -> TripRunResultResponse:
    try:
        return service.result(run_id)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/{run_id}/comparison", response_model=PlanComparisonResponse)
async def compare_trip_run_plans(run_id: str, service: RunService) -> PlanComparisonResponse:
    try:
        return service.comparison(run_id)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    except RunConflictError as exc:
        raise _conflict(exc) from exc
