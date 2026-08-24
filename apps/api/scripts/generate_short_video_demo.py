from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models import Asset, Shot  # noqa: E402
from app.services.asset_candidate_service import promote_asset_candidate  # noqa: E402
from app.services.asset_service import generate_single_shot_video_candidate  # noqa: E402


DEFAULT_DURATIONS = ("2", "3", "2", "3", "2", "3", "2")


def generate_demo(project_id: str, durations: list[Decimal]) -> dict[str, Any]:
    with SessionLocal() as db:
        shots = list(
            db.scalars(
                select(Shot)
                .where(Shot.project_id == project_id)
                .where(Shot.is_current.is_(True))
                .where(Shot.status == "approved")
                .order_by(Shot.shot_no)
            ).all()
        )
        if not shots:
            raise SystemExit(f"No approved shots found for project {project_id}")
        if len(durations) < len(shots):
            durations = [*durations, *([durations[-1]] * (len(shots) - len(durations)))]

        results: list[dict[str, Any]] = []
        for index, shot in enumerate(shots):
            duration = durations[index]
            print(f"[demo] shot {shot.shot_no}: generating {duration}s candidate", flush=True)
            candidate, task = generate_single_shot_video_candidate(
                db,
                shot.id,
                duration_sec=duration,
                video_prompt=shot.video_prompt,
            )
            print(f"[demo] shot {shot.shot_no}: candidate {candidate.id} generated", flush=True)
            asset = promote_asset_candidate(
                db,
                candidate.id,
                review_note="Codex 2-3s real-chain demo candidate",
            )
            print(f"[demo] shot {shot.shot_no}: promoted asset {asset.id}", flush=True)
            results.append(
                {
                    "shot_no": shot.shot_no,
                    "shot_id": shot.id,
                    "duration_sec": str(duration),
                    "candidate_id": candidate.id,
                    "asset_id": asset.id,
                    "asset_uri": asset.uri,
                    "mime_type": asset.mime_type,
                    "width": asset.width,
                    "height": asset.height,
                    "task_id": task.id,
                }
            )

        selected_assets = list(
            db.scalars(
                select(Asset)
                .where(Asset.project_id == project_id)
                .where(Asset.asset_type == "video")
                .where(Asset.asset_role == "shot_video")
                .where(Asset.entity_type == "shot")
                .where(Asset.is_selected.is_(True))
                .order_by(Asset.created_at)
            ).all()
        )
        return {
            "project_id": project_id,
            "generated": results,
            "selected_video_assets": [
                {
                    "asset_id": asset.id,
                    "entity_id": asset.entity_id,
                    "uri": asset.uri,
                    "duration_sec": str(asset.duration_sec) if asset.duration_sec is not None else None,
                    "width": asset.width,
                    "height": asset.height,
                }
                for asset in selected_assets
            ],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate 2-3s real Wan I2V demo clips for an AVD project.")
    parser.add_argument("project_id")
    parser.add_argument(
        "--durations",
        default=",".join(DEFAULT_DURATIONS),
        help="Comma-separated per-shot durations in seconds. Defaults to 2,3,2,3,2,3,2.",
    )
    parser.add_argument("--output", default="", help="Optional JSON output path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    durations = [Decimal(item.strip()) for item in args.durations.split(",") if item.strip()]
    if not durations:
        raise SystemExit("At least one duration is required")
    result = generate_demo(args.project_id, durations)
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
