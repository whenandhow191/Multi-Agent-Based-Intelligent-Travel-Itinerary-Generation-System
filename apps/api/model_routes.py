"""Model discovery and explicit low-cost connectivity probe endpoints."""

from fastapi import APIRouter, HTTPException, status

from apps.api.model_models import ModelCatalogResponse, ModelProbeRequest, ModelProbeResponse
from apps.api.model_service import PenguinModelService
from apps.api.settings import get_settings
from packages.models.adapters import ModelProviderError

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("", response_model=ModelCatalogResponse)
async def list_models() -> ModelCatalogResponse:
    return await PenguinModelService(get_settings()).catalog()


@router.post("/probe", response_model=ModelProbeResponse)
async def probe_model(payload: ModelProbeRequest) -> ModelProbeResponse:
    try:
        return await PenguinModelService(get_settings()).probe(payload.model_id)
    except ModelProviderError as exc:
        code = exc.kind.value
        http_status = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.retryable
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": f"model_{code}", "message": f"模型连接测试失败：{code}"},
        ) from exc
