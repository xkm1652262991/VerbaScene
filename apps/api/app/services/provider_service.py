from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import GenerationTask
from app.providers.defaults import provider_registry
from app.providers.types import ProviderRequest, ProviderResponse, ProviderStatus, ProviderType
from app.schemas.provider import ProviderTestRequest
from app.services.task_service import mark_task_failed, mark_task_running, mark_task_succeeded


def list_provider_descriptors() -> list[dict]:
    selected = {
        ProviderType.LLM: settings.llm_provider,
        ProviderType.IMAGE: settings.image_provider,
        ProviderType.VIDEO: settings.video_provider,
    }
    descriptors = []
    for provider in provider_registry.list():
        descriptor = provider.descriptor()
        descriptor["selected"] = selected.get(provider.type) == provider.name
        try:
            provider.validate_config()
        except ValueError as exc:
            descriptor["configuration_status"] = "incomplete"
            descriptor["validation_error"] = str(exc)
        else:
            descriptor["configuration_status"] = "ready"
            descriptor["validation_error"] = None
        descriptors.append(descriptor)
    return descriptors


def submit_provider_test(
    db: Session,
    payload: ProviderTestRequest,
) -> tuple[ProviderResponse, GenerationTask | None]:
    provider_type = ProviderType(payload.provider_type)
    provider = provider_registry.get(provider_type, payload.provider_name)
    model = payload.model or provider.model

    task = None
    task_id = "provider-test"

    if payload.project_id:
        task = GenerationTask(
            project_id=payload.project_id,
            task_type=f"{provider_type.value}_test",
            provider=provider.name,
            model=model,
            input_payload=payload.model_dump(),
            status=ProviderStatus.RUNNING.value,
            started_at=datetime.now(timezone.utc),
        )
        db.add(task)
        db.flush()
        mark_task_running(db, task, "正在测试 Provider", 20)
        task_id = task.id

    request = ProviderRequest(
        project_id=payload.project_id or "provider-test",
        task_id=task_id,
        model=model,
        prompt=payload.prompt,
        negative_prompt=payload.negative_prompt,
        references=payload.references,
        params=payload.params,
        metadata=payload.metadata,
    )

    response = provider.submit(request)

    if task is not None:
        task.provider_task_id = response.provider_task_id
        task.cost_estimate = Decimal(response.usage.cost)
        if response.error is not None:
            mark_task_failed(
                db,
                task,
                code=response.error.error_code,
                message=response.error.error_message,
                raw_response=response.raw_response,
                provider_task_id=response.provider_task_id,
            )
        else:
            mark_task_succeeded(
                db,
                task,
                output_asset_ids=[],
                raw_response=response.raw_response,
                provider_task_id=response.provider_task_id,
            )
        db.add(task)
        db.commit()
        db.refresh(task)

    return response, task
