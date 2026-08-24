from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


COMMON_ERROR_MESSAGES: dict[str, str] = {
    "bad_request": "请求参数不正确。",
    "not_found": "没有找到对应资源。",
    "conflict": "当前状态不允许执行这个操作。",
    "validation_error": "请求参数校验失败。",
    "internal_error": "服务内部错误。",
}


def normalize_error_detail(detail: Any, fallback_status: int) -> tuple[str, str, Any]:
    if isinstance(detail, dict):
        code = str(detail.get("code") or _code_for_status(fallback_status))
        message = str(detail.get("message") or COMMON_ERROR_MESSAGES.get(code) or detail.get("detail") or code)
        return code, message, detail
    if isinstance(detail, str) and detail:
        code = _code_for_status(fallback_status)
        return code, detail, None
    code = _code_for_status(fallback_status)
    return code, COMMON_ERROR_MESSAGES.get(code, "请求失败。"), detail


def register_exception_handlers(app) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        code, message, detail = normalize_error_detail(exc.detail, exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": code,
                    "message": message,
                    "detail": detail,
                },
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "validation_error",
                    "message": COMMON_ERROR_MESSAGES["validation_error"],
                    "detail": exc.errors(),
                },
            },
        )


def _code_for_status(status_code: int) -> str:
    if status_code == status.HTTP_404_NOT_FOUND:
        return "not_found"
    if status_code == status.HTTP_409_CONFLICT:
        return "conflict"
    if status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
        return "validation_error"
    if status_code >= 500:
        return "internal_error"
    return "bad_request"
