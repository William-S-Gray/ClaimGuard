"""Authentication service: login, refresh-token rotation, user provisioning."""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from app.core.redis import redis_client
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.repositories.user import (
    RefreshTokenRepository,
    RoleRepository,
    UserRepository,
)
from app.schemas.auth import TokenPair


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.tokens = RefreshTokenRepository(session)

    async def authenticate(self, email: str, password: str) -> User:
        user = await self.users.get_by_email(email.lower())
        if not user or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Invalid email or password")
        if not user.is_active:
            raise AuthenticationError("Account is disabled")
        return user

    async def _issue_tokens(self, user: User) -> dict:
        access = create_access_token(
            str(user.id),
            roles=user.role_names,
            permissions=user.permission_codes,
            provider_id=str(user.provider_id) if user.provider_id else None,
        )
        refresh, jti, expires = create_refresh_token(str(user.id))
        await self.tokens.create(user_id=user.id, jti=jti, expires_at=expires)
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            token_type="bearer",
            expires_in=settings.access_token_expire_minutes * 60,
        ).model_dump(by_alias=True)

    @staticmethod
    def _lock_key(email: str, ip: str) -> str:
        return f"authlock:{email.lower()}:{ip}"

    async def login(self, email: str, password: str, ip: str = "unknown") -> dict:
        key = self._lock_key(email, ip)
        try:
            fails = int(await redis_client.client.get(key) or 0)
        except Exception:
            fails = 0
        if fails >= settings.auth_max_failures:
            raise RateLimitError(
                "Too many failed sign-in attempts. Please try again later."
            )

        try:
            user = await self.authenticate(email, password)
        except AuthenticationError:
            # Count the failure and (re)set the lockout window.
            try:
                count = await redis_client.client.incr(key)
                if count == 1:
                    await redis_client.client.expire(key, settings.auth_lockout_seconds)
            except Exception:
                pass
            raise

        # Success — clear any failure counter.
        with contextlib.suppress(Exception):
            await redis_client.client.delete(key)
        user.last_login_at = datetime.now(UTC)
        await self.session.flush()
        return await self._issue_tokens(user)

    async def refresh(self, refresh_token: str) -> dict:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid or expired refresh token") from exc

        jti = payload.get("jti")
        stored = await self.tokens.get_by_jti(jti) if jti else None
        if not stored or stored.revoked:
            raise AuthenticationError("Refresh token has been revoked")
        if stored.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
            raise AuthenticationError("Refresh token has expired")

        user = await self.users.get(payload["sub"])
        if not user or not user.is_active:
            raise AuthenticationError("User no longer active")

        stored.revoked = True  # rotate: one-time use
        await self.session.flush()
        return await self._issue_tokens(user)

    async def logout(self, refresh_token: str) -> None:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.PyJWTError:
            return
        stored = await self.tokens.get_by_jti(payload.get("jti", ""))
        if stored:
            stored.revoked = True
            await self.session.flush()

    async def create_user(
        self, email: str, full_name: str, password: str, role_names: list[str]
    ) -> User:
        if await self.users.get_by_email(email.lower()):
            raise ConflictError("A user with that email already exists")
        roles = await self.roles.get_many_by_name(role_names)
        user = User(
            email=email.lower(),
            full_name=full_name,
            hashed_password=hash_password(password),
            is_active=True,
        )
        user.roles = roles
        return await self.users.add(user)

    async def _resolve_roles(self, role_names: list[str]) -> list:
        if not role_names:
            raise ValidationError("A user must have at least one role")
        roles = await self.roles.get_many_by_name(role_names)
        missing = set(role_names) - {r.name for r in roles}
        if missing:
            raise ValidationError(f"Unknown role(s): {', '.join(sorted(missing))}")
        return roles

    async def update_user(
        self,
        user_id: str,
        *,
        actor_id: str,
        full_name: str | None = None,
        role_names: list[str] | None = None,
        is_active: bool | None = None,
    ) -> User:
        """Edit / (de)activate a user, guarding against admin lock-out."""
        user = await self.users.get(user_id)
        if not user:
            raise NotFoundError("User not found")

        is_self = str(user.id) == str(actor_id)
        currently_active_admin = user.is_active and "admin" in user.role_names
        resulting_active = user.is_active if is_active is None else bool(is_active)
        resulting_roles = user.role_names if role_names is None else role_names
        resulting_active_admin = resulting_active and "admin" in resulting_roles

        if is_self and is_active is False:
            raise BusinessRuleError("You cannot deactivate your own account")
        if is_self and role_names is not None and "admin" not in resulting_roles:
            raise BusinessRuleError("You cannot remove your own admin role")
        if (
            currently_active_admin
            and not resulting_active_admin
            and await self.users.count_active_admins(exclude_id=user.id) == 0
        ):
            raise BusinessRuleError("At least one active administrator is required")

        roles = await self._resolve_roles(role_names) if role_names is not None else None

        if full_name is not None:
            trimmed = full_name.strip()
            if trimmed:
                user.full_name = trimmed
        if is_active is not None:
            user.is_active = bool(is_active)
        await self.session.flush()
        if roles is not None:
            await self.users.set_roles(user, roles)
            # Reload the collection in-context; the Core writes above bypass the ORM,
            # leaving user.roles stale. refresh() loads eagerly under the awaited call.
            await self.session.refresh(user, attribute_names=["roles"])
        return user

    async def delete_user(self, user_id: str, *, actor_id: str) -> None:
        """Permanently delete a user, guarding self-deletion and last-admin."""
        user = await self.users.get(user_id)
        if not user:
            raise NotFoundError("User not found")
        if str(user.id) == str(actor_id):
            raise BusinessRuleError("You cannot delete your own account")
        if (
            user.is_active
            and "admin" in user.role_names
            and await self.users.count_active_admins(exclude_id=user.id) == 0
        ):
            raise BusinessRuleError("At least one active administrator is required")
        await self.users.hard_delete_user(user)
