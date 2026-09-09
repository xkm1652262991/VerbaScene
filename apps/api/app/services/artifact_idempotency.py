from __future__ import annotations

from hashlib import sha256
import json


def artifact_completion_key(
    source_task_id: str | None,
    *,
    artifact_kind: str,
    asset_type: str,
    asset_role: str | None,
    entity_type: str | None,
    entity_id: str | None,
    variant_key: str | None = None,
    candidate_type: str | None = None,
) -> str | None:
    """Return a stable key for one logical artifact produced by one task."""
    if not source_task_id:
        return None
    scope = {
        "artifact_kind": artifact_kind,
        "asset_type": asset_type,
        "asset_role": asset_role,
        "candidate_type": candidate_type,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "variant_key": variant_key,
    }
    digest = sha256(
        json.dumps(scope, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:32]
    return f"task:{source_task_id}:{artifact_kind}:{digest}"
