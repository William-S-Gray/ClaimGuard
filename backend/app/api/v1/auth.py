"""Authentication endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.core.config import settings
from app.core.dependencies import (
    CurrentUser,
    CurrentUserDep,
    DbSession,
    client_ip,
    require_roles,
)
from app.core.responses import success
from app.repositories.user import UserRepository
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    UserCreate,
    UserOut,
    UserSummary,
    UserUpdate,
)
from app.services.audit import AuditService
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

AdminDep = Annotated[CurrentUser, Depends(require_roles("admin"))]


def _user_summary(user) -> dict:  # noqa: ANN001
    return UserSummary(
        id=str(user.id),
        full_name=user.full_name,
        email=user.email,
        roles=user.role_names,
        is_active=user.is_active,
    ).model_dump(by_alias=True)


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.refresh_token_expire_days * 86400,
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path="/api/v1/auth",
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def _refresh_from(request: Request, payload: RefreshRequest | None) -> str:
    """Prefer the httpOnly cookie; fall back to a body token (API clients)."""
    cookie = request.cookies.get(settings.refresh_cookie_name)
    if cookie:
        return cookie
    return payload.refresh_token if payload and payload.refresh_token else ""


@router.post("/login", summary="Authenticate and receive tokens")
async def login(
    payload: LoginRequest, request: Request, response: Response, db: DbSession
) -> dict:
    service = AuthService(db)
    tokens = await service.login(payload.email, payload.password, ip=client_ip(request))
    _set_refresh_cookie(response, tokens["refreshToken"])
    await AuditService(db).record(
        action="auth.login",
        entity_type="user",
        actor_email=payload.email,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
    )
    return success(tokens, "Login successful")


@router.post("/refresh", summary="Rotate refresh token")
async def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    payload: RefreshRequest | None = None,
) -> dict:
    tokens = await AuthService(db).refresh(_refresh_from(request, payload))
    _set_refresh_cookie(response, tokens["refreshToken"])
    return success(tokens, "Token refreshed")


@router.post("/logout", summary="Revoke refresh token")
async def logout(
    request: Request,
    response: Response,
    db: DbSession,
    payload: RefreshRequest | None = None,
) -> dict:
    await AuthService(db).logout(_refresh_from(request, payload))
    _clear_refresh_cookie(response)
    return success(None, "Logged out")


@router.get("/me", summary="Current authenticated user")
async def me(user: CurrentUserDep, db: DbSession) -> dict:
    db_user = await UserRepository(db).get(user.id)
    if not db_user:
        return success(None, "User not found")
    out = UserOut(
        id=str(db_user.id),
        email=db_user.email,
        full_name=db_user.full_name,
        is_active=db_user.is_active,
        is_superuser=db_user.is_superuser,
        roles=db_user.role_names,
        permissions=db_user.permission_codes,
        provider_id=str(db_user.provider_id) if db_user.provider_id else None,
        last_login_at=db_user.last_login_at,
    )
    return success(out.model_dump(by_alias=True), "OK")


@router.post(
    "/users",
    summary="Create a user (admin only)",
    dependencies=[Depends(require_roles("admin"))],
)
async def create_user(payload: UserCreate, db: DbSession) -> dict:
    user = await AuthService(db).create_user(
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        role_names=payload.roles,
    )
    return success({"id": str(user.id), "email": user.email}, "User created")


@router.patch("/users/{user_id}", summary="Update a user (admin only)")
async def update_user(
    user_id: str, payload: UserUpdate, admin: AdminDep, db: DbSession
) -> dict:
    user = await AuthService(db).update_user(
        user_id,
        actor_id=admin.id,
        full_name=payload.full_name,
        role_names=payload.roles,
        is_active=payload.is_active,
    )
    return success(_user_summary(user), "User updated")


@router.delete("/users/{user_id}", summary="Delete a user (admin only)")
async def delete_user(user_id: str, admin: AdminDep, db: DbSession) -> dict:
    await AuthService(db).delete_user(user_id, actor_id=admin.id)
    return success(None, "User deleted")
