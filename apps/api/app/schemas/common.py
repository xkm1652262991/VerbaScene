from typing import Generic, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


class ApiError(BaseModel):
    code: str = "request_failed"
    message: str
    detail: object | None = None


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T


class ErrorResponse(BaseModel):
    success: bool = False
    error: ApiError


class PageMeta(BaseModel):
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


class PageResponse(BaseModel, Generic[T]):
    success: bool = True
    items: list[T]
    meta: PageMeta
