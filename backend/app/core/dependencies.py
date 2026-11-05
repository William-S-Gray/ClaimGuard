"""FastAPI dependencies: DB session, current user, RBAC guards, pagination."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


# Internal (insurer) roles. Everything under the staff routers needs one of them;
# provider-portal accounts never have one, so they are locked out by default.
STAFF_ROLES = {"admin", "analyst", "agent", "auditor"}


@dataclass(slots=True)
class CurrentUser:
    id: str
    roles: list[str]
    permissions: list[str]
    provider_id: str | None = None

    @property
    def is_staff(self) -> bool:
        return self.provider_id is None and self.has_any_role(STAFF_ROLES)

    @property
    def is_superuser(self) -> bool:
        return "*" in self.permissions

    def has_permission(self, code: str) -> bool:
        return self.is_superuser or code in self.permissions

    def has_any_role(self, roles: set[str]) -> bool:
        return self.is_superuser or bool(set(self.roles) & roles)


async def load_current_user(session: AsyncSession, user_id: str) -> CurrentUser:
    """The user as they are *now*, not as they were when the token was minted.

    Roles and the active flag come from the database on every request, so a
    deactivation or role change takes effect immediately instead of when the
    access token happens to expire.
    """
    from app.repositories.user import UserRepository  # local: avoids an import cycle

    user = await UserRepository(session).get(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is disabled or no longer exists")
    return CurrentUser(
        id=str(user.id),
        roles=user.role_names,
        permissions=user.permission_codes,
        provider_id=str(user.provider_id) if user.provider_id else None,
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: DbSession,
) -> CurrentUser:
    if credentials is None:
        raise AuthenticationError("Missing bearer token")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid access token") from exc
    return await load_current_user(session, payload["sub"])


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def get_staff_user(user: CurrentUserDep) -> CurrentUser:
    """Insurer staff only. Guards every PHI/operations router."""
    if not user.is_staff:
        raise PermissionDeniedError("Staff access only")
    return user


async def get_provider_user(user: CurrentUserDep) -> CurrentUser:
    """Provider-portal accounts only; `provider_id` scopes every query."""
    if not user.provider_id or "provider" not in user.roles:
        raise PermissionDeniedError("Provider portal access only")
    return user


ProviderUserDep = Annotated[CurrentUser, Depends(get_provider_user)]


def require_roles(*roles: str) -> Callable:
    required = set(roles)

    async def _guard(user: CurrentUserDep) -> CurrentUser:
        if not user.has_any_role(required):
            raise PermissionDeniedError(
                f"Requires one of roles: {', '.join(sorted(required))}"
            )
        return user

    return _guard


def require_permission(code: str) -> Callable:
    async def _guard(user: CurrentUserDep) -> CurrentUser:
        if not user.has_permission(code):
            raise PermissionDeniedError(f"Requires permission: {code}")
        return user

    return _guard


@dataclass(slots=True)
class Pagination:
    page: int
    page_size: int


def pagination_params(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    page_size: int = Query(
        10, ge=1, le=200,
        description="Items per page (UI offers 5-50; the app also batch-fetches 200)",
    ),
) -> Pagination:
    return Pagination(page=page, page_size=page_size)


PaginationDep = Annotated[Pagination, Depends(pagination_params)]


def client_ip(request: Request) -> str:
    """The real client IP, read only from what a trusted proxy wrote.

    Rate limiting, login lockout and the audit log key on this, so a value the
    client can choose would let it dodge all three. Proxies *append* to
    X-Forwarded-For and pass the client's own entries through, so the left-most
    entry (and X-Real-IP, which Render doesn't overwrite) is attacker-controlled.
    See `client_ip_header` / `forwarded_ip_hops` in settings.
    """
    if settings.client_ip_header:
        value = request.headers.get(settings.client_ip_header, "").strip()
        if value:
            return value
    hops = settings.forwarded_ip_hops
    forwarded = request.headers.get("x-forwarded-for")
    if hops and forwarded:
        entries = [e.strip() for e in forwarded.split(",") if e.strip()]
        if len(entries) >= hops:
            return entries[-hops]
    return request.client.host if request.client else "unknown"
