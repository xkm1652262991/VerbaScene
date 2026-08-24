from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
DEFAULT_CONTRACT = REPO_ROOT / "docs" / "api" / "openapi.json"

sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(API_ROOT / "scripts"))

from export_openapi import build_openapi_schema, stable_json  # noqa: E402

REQUIRED_ERROR_CODES = ("400", "404", "409", "422", "500")
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the committed OpenAPI contract.")
    parser.add_argument(
        "--contract",
        default=str(DEFAULT_CONTRACT),
        help="Contract file to compare. Defaults to docs/api/openapi.json.",
    )
    args = parser.parse_args()
    contract_path = Path(args.contract).resolve()
    schema = build_openapi_schema()
    errors = validate_schema(schema)

    if not contract_path.exists():
        errors.append(f"Missing OpenAPI contract: {contract_path}")
    else:
        expected = stable_json(schema)
        actual = contract_path.read_text(encoding="utf-8")
        if actual != expected:
            errors.append(
                "OpenAPI contract is stale. Run: cd apps/api && .venv/bin/python scripts/export_openapi.py"
            )

    if errors:
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    print(f"OpenAPI contract is current: {contract_path}")


def validate_schema(schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schemas = schema.get("components", {}).get("schemas", {})
    for name in ("ApiError", "ErrorResponse", "PageMeta"):
        if name not in schemas:
            errors.append(f"Missing common schema component: {name}")

    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {})
            for status_code in REQUIRED_ERROR_CODES:
                response = responses.get(status_code)
                if not _uses_error_response(response):
                    errors.append(f"{method.upper()} {path} missing ErrorResponse for {status_code}")
    return errors


def _uses_error_response(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    schema = (
        response.get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    return schema == {"$ref": "#/components/schemas/ErrorResponse"}


if __name__ == "__main__":
    main()
