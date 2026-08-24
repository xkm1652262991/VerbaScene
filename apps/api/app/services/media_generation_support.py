"""Shared task, audit, progress, and error helpers for media generation."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.avd_asset_strategy import avd_asset_metadata
from app.models import Asset, AssetCandidate, GenerationTask
from app.providers.types import ProviderStatus
from app.services.task_service import mark_task_running, mark_task_succeeded

def create_generation_task(
    db: Session,
    *,
    project_id: str,
    task_type: str,
    input_payload: dict,
    provider: str = "mock",
    model: str = "mock-image",
) -> GenerationTask:
    task = GenerationTask(
        project_id=project_id,
        task_type=task_type,
        provider=provider,
        model=model,
        input_payload=input_payload,
        status=ProviderStatus.RUNNING.value,
        started_at=datetime.now(timezone.utc),
    )
    db.add(task)
    db.flush()
    mark_task_running(db, task, "任务已开始", 5)
    return task


def with_avd_asset_usage(
    metadata: dict | None,
    *,
    asset_type: str,
    asset_role: str | None,
    entity_type: str | None,
    variant_key: str | None = None,
) -> dict:
    updated = dict(metadata or {})
    if variant_key is not None:
        updated.setdefault("variant_key", variant_key)
    usage = avd_asset_metadata(asset_type=asset_type, asset_role=asset_role, entity_type=entity_type)
    updated.setdefault("avd_asset_purpose", usage["asset_purpose"])
    updated.setdefault("avd_asset_usage", usage)
    return updated


def sanitize_large_payload(value: object) -> object:
    if isinstance(value, dict):
        return {key: _sanitize_large_payload_by_key(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_large_payload(item) for item in value]
    if isinstance(value, str) and (len(value) > 4096 or value.startswith("data:")):
        return f"<omitted {len(value)} chars>"
    return value


def _sanitize_large_payload_by_key(key: object, value: object) -> object:
    key_text = str(key).lower()
    if isinstance(value, str) and key_text in {"b64_json", "base64", "image", "image_base64", "audio", "video"}:
        return f"<omitted {len(value)} chars>"
    return sanitize_large_payload(value)


def finish_generation_task(db: Session, task: GenerationTask, assets: list[Asset]) -> None:
    payload = {
        "asset_ids": [asset.id for asset in assets],
        "asset_count": len(assets),
    }
    generation_seeds = _recorded_generation_seeds(assets)
    if generation_seeds:
        payload["generation_seeds"] = generation_seeds
    mark_task_succeeded(
        db,
        task,
        output_asset_ids=[asset.id for asset in assets],
        result_payload=payload,
        raw_response=payload,
    )
    db.add(task)
    db.commit()

    for asset in assets:
        db.refresh(asset)
    db.refresh(task)


def finish_candidate_generation_task(db: Session, task: GenerationTask, candidates: list[AssetCandidate]) -> None:
    payload = {
        "candidate_ids": [candidate.id for candidate in candidates],
        "candidate_count": len(candidates),
        "candidate_policy": "pending_review_before_asset",
    }
    generation_seeds = _recorded_generation_seeds(candidates)
    if generation_seeds:
        payload["generation_seeds"] = generation_seeds
    mark_task_succeeded(
        db,
        task,
        output_asset_ids=[],
        result_payload=payload,
        raw_response=payload,
    )
    db.add(task)
    db.commit()

    for candidate in candidates:
        db.refresh(candidate)
    db.refresh(task)


def _recorded_generation_seeds(records: list[Asset] | list[AssetCandidate]) -> list[int]:
    seeds: list[int] = []
    for record in records:
        raw_response = record.raw_response if isinstance(record.raw_response, dict) else {}
        request_metadata = raw_response.get("request_metadata")
        if not isinstance(request_metadata, dict):
            continue
        value = request_metadata.get("generation_seed")
        if isinstance(value, int):
            seeds.append(value)
    return seeds


def batch_progress(completed: int, total: int) -> int:
    if total <= 0:
        return 10
    return 10 + round((completed / total) * 80)


def extract_task_error(exc: Exception) -> tuple[str | None, str, dict | None]:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            code = detail.get("code")
            message = detail.get("message") or str(detail)
            return str(code) if code else None, str(message), detail
        return None, str(detail), None
    return None, str(exc), None
