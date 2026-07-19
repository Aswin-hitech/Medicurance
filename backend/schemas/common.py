from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


class APIError(BaseModel):
    code: str = Field(default="error")
    message: str


class APIEnvelope(BaseModel, Generic[T]):
    success: bool
    message: str = ""
    data: T | None = None
    error: APIError | None = None
    meta: dict[str, Any] | None = None


class AuthStatusData(BaseModel):
    authenticated: bool
    role: str | None = None
    mobile: str | None = None
    user_id: str | None = None


class RoleRBAC(BaseModel):
    role: str

