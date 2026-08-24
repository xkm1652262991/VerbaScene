from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from app.models import GenerationTask
from app.platform.media import get_media_store
from app.platform.media.store import suffix_for_mime
from app.providers.base import ProviderAdapter
from app.providers.types import ProviderAsset, ProviderRequest, ProviderResponse


def provider_request_from_task(task: GenerationTask) -> ProviderRequest:
    payload = task.input_payload if isinstance(task.input_payload, dict) else {}
    frozen = payload.get("provider_request")
    if not isinstance(frozen, dict):
        raise ValueError("Video task does not contain a frozen provider request")
    return ProviderRequest(
        project_id=task.project_id,
        task_id=f"{task.id}:video-candidate",
        model=str(frozen.get("model") or task.model or ""),
        prompt=str(frozen.get("prompt") or ""),
        negative_prompt=(
            str(frozen.get("negative_prompt"))
            if frozen.get("negative_prompt") is not None
            else None
        ),
        references=[str(value) for value in frozen.get("references") or []],
        params=dict(frozen.get("params") or {}),
        metadata=dict(frozen.get("metadata") or {}),
    )


def fetch_provider_result(
    provider: ProviderAdapter,
    provider_task_id: str,
    request: ProviderRequest,
    provider_context: dict,
) -> ProviderResponse:
    submission = (
        provider_context.get("provider_submission")
        if isinstance(provider_context.get("provider_submission"), dict)
        else {}
    )
    return provider.fetch_result(
        provider_task_id,
        request=request,
        provider_context=submission,
    )


def persist_provider_temp_files(
    project_id: str,
    task_id: str,
    response: ProviderResponse,
) -> ProviderResponse:
    """Move provider-owned temporary results through the MediaStore boundary."""
    store = get_media_store()
    assets: list[ProviderAsset] = []
    for asset in response.assets:
        metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
        source = Path(asset.uri).expanduser()
        if metadata.get("temporary_file") or source.is_file():
            suffix = source.suffix or suffix_for_mime(asset.mime_type, ".mp4")
            uri = store.put_file(
                project_id,
                source,
                namespace="assets/video-candidates",
                filename=f"{task_id}-{uuid4().hex}{suffix}",
            )
            if metadata.get("temporary_file"):
                source.unlink(missing_ok=True)
            assets.append(
                replace(
                    asset,
                    uri=uri,
                    metadata={**metadata, "temporary_file": False},
                )
            )
        else:
            assets.append(asset)
    return replace(response, assets=assets)
