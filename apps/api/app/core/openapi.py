from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel

from app.schemas.common import ApiError, ErrorResponse, PageMeta


ERROR_RESPONSES: dict[str, str] = {
    "400": "Bad request",
    "404": "Resource not found",
    "409": "State conflict",
    "422": "Validation error",
    "500": "Internal server error",
}

HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head"}


def install_openapi_contract(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
            description=app.description,
        )
        _ensure_common_schemas(schema)
        _ensure_error_responses(schema)
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi


def _ensure_common_schemas(schema: dict[str, Any]) -> None:
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for model in (ApiError, ErrorResponse, PageMeta):
        _add_model_schema(components, model)


def _add_model_schema(components: dict[str, Any], model: type[BaseModel]) -> None:
    model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    defs = model_schema.pop("$defs", {})
    for name, definition in defs.items():
        components.setdefault(name, definition)
    components.setdefault(model.__name__, model_schema)


def _ensure_error_responses(schema: dict[str, Any]) -> None:
    error_content = {
        "application/json": {
            "schema": {"$ref": "#/components/schemas/ErrorResponse"},
        },
    }
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            for status_code, description in ERROR_RESPONSES.items():
                responses[status_code] = {
                    "description": responses.get(status_code, {}).get("description", description),
                    "content": error_content,
                }
