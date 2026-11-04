"""Auth & identity schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.common import CamelModel


class LoginRequest(CamelModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(CamelModel):
    # Optional: the refresh token normally arrives via the httpOnly cookie.
    refresh_token: str | None = None


class TokenPair(CamelModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(CamelModel):
    id: str
    email: EmailStr
    full_name: str
    is_active: bool
    is_superuser: bool
    roles: list[str] = []
    permissions: list[str] = []
    provider_id: str | None = None  # set for provider-portal accounts
    last_login_at: datetime | None = None


class UserCreate(CamelModel):
    email: EmailStr
    full_name: str
    password: str = Field(min_length=8)
    roles: list[str] = ["agent"]


class UserUpdate(CamelModel):
    """Partial update for an existing account (admin only)."""

    full_name: str | None = Field(default=None, min_length=1)
    roles: list[str] | None = None
    is_active: bool | None = None


class UserSummary(CamelModel):
    """Lightweight user for directory / assignment pickers."""

    id: str
    full_name: str
    email: EmailStr
    roles: list[str] = []
    is_active: bool = True
