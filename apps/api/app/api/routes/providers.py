from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.provider import (
    ImageProviderProfileRead,
    ProviderConfigUpsert,
    ProviderDescriptor,
    ProviderTestRequest,
    ProviderTestResponse,
    RuntimeProviderConfigRead,
)
from app.services.image_provider_profile_service import list_image_provider_profiles
from app.services.provider_config_service import (
    list_runtime_provider_configs,
    reset_runtime_provider_config,
    upsert_runtime_provider_config,
)
from app.services.provider_service import list_provider_descriptors, submit_provider_test

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("", response_model=PageResponse[ProviderDescriptor])
def list_providers_endpoint(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
) -> PageResponse[ProviderDescriptor]:
    providers = list_provider_descriptors()
    return PageResponse(items=providers[offset : offset + limit], meta={"total": len(providers), "offset": offset, "limit": limit})


@router.get("/configs", response_model=PageResponse[RuntimeProviderConfigRead])
def list_provider_configs_endpoint(
    db: Session = Depends(get_db),
) -> PageResponse[RuntimeProviderConfigRead]:
    configs = list_runtime_provider_configs(db)
    return PageResponse(items=configs, meta={"total": len(configs), "offset": 0, "limit": len(configs)})


@router.get("/image-profiles", response_model=PageResponse[ImageProviderProfileRead])
def list_image_provider_profiles_endpoint(
    db: Session = Depends(get_db),
) -> PageResponse[ImageProviderProfileRead]:
    profiles = list_image_provider_profiles(db)
    return PageResponse(items=profiles, meta={"total": len(profiles), "offset": 0, "limit": len(profiles)})


@router.put("/configs/{provider_type}", response_model=ApiResponse[RuntimeProviderConfigRead])
def upsert_provider_config_endpoint(
    provider_type: str,
    payload: ProviderConfigUpsert,
    db: Session = Depends(get_db),
) -> ApiResponse[RuntimeProviderConfigRead]:
    return ApiResponse(data=upsert_runtime_provider_config(db, provider_type, payload))


@router.delete("/configs/{provider_type}", response_model=ApiResponse[RuntimeProviderConfigRead])
def reset_provider_config_endpoint(
    provider_type: str,
    db: Session = Depends(get_db),
) -> ApiResponse[RuntimeProviderConfigRead]:
    return ApiResponse(data=reset_runtime_provider_config(db, provider_type))


@router.post("/test", response_model=ApiResponse[ProviderTestResponse])
def test_provider_endpoint(
    payload: ProviderTestRequest,
    db: Session = Depends(get_db),
) -> ApiResponse[ProviderTestResponse]:
    response, task = submit_provider_test(db, payload)
    return ApiResponse(
        data={
            "status": response.status.value,
            "provider_task_id": response.provider_task_id,
            "execution_mode": response.execution_mode.value,
            "poll_after_sec": response.poll_after_sec,
            "task_id": task.id if task else None,
            "assets": [
                {
                    "asset_type": asset.asset_type,
                    "uri": asset.uri,
                    "mime_type": asset.mime_type,
                    "width": asset.width,
                    "height": asset.height,
                    "duration_sec": asset.duration_sec,
                    "metadata": asset.metadata,
                }
                for asset in response.assets
            ],
            "raw_response": response.raw_response,
            "usage": {
                "cost": response.usage.cost,
                "unit": response.usage.unit,
            },
            "error": response.error,
            "created_at": task.created_at if task else None,
        }
    )
