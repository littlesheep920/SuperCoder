"""Shared API models: error envelope, pagination, and generic values."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ErrorBody", "PageInfo", "Page", "JsonObject"]

T = TypeVar("T")

JsonObject = dict[str, Any]


class PageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(ge=1, le=100)
    has_more: bool
    next_cursor: str | None = None


class Page(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    items: list[T]
    page: PageInfo


class ErrorBody(BaseModel):
    """RFC 9457 compatible ``application/problem+json`` body."""

    model_config = ConfigDict(extra="forbid")

    type: str = "/problems/internal-error"
    title: str = "An error occurred"
    status: int
    code: str
    detail: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    suggestion: str | None = None
    retryable: bool = False
