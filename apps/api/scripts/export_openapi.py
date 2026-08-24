from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "api" / "openapi.json"

sys.path.insert(0, str(API_ROOT))

from app.main import create_app  # noqa: E402


def build_openapi_schema() -> dict[str, Any]:
    app = create_app()
    return app.openapi()


def stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def export_openapi(output_path: Path = DEFAULT_OUTPUT) -> Path:
    schema = build_openapi_schema()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(stable_json(schema), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the FastAPI OpenAPI contract.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output path. Defaults to docs/api/openapi.json.",
    )
    args = parser.parse_args()
    output_path = export_openapi(Path(args.output).resolve())
    print(output_path)


if __name__ == "__main__":
    main()
