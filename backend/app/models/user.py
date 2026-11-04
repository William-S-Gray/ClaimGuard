"""Identity & access: User, Role, Permission (RBAC) + RefreshToken rotation."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, BaseEntity

# ── Association tables ───────────────────────────────────────────────────────────
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", GUID, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", GUID, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", GUID, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        GUID,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Permission(BaseEntity):
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255))

    roles: Mapped[list[Role]] = relationship(
        secondary=role_permissions, back_populates="permissions"
    )


class Role(BaseEntity):
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255))

    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, back_populates="roles", lazy="selectin"
    )
    users: Mapped[list[User]] = relationship(
        secondary=user_roles, back_populates="roles"
    )


class User(BaseEntity):
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set only for provider-portal accounts: the one provider whose data they see.
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("providers.id", ondelete="SET NULL"), index=True, nullable=True
    )

    roles: Mapped[list[Role]] = relationship(
        secondary=user_roles, back_populates="users", lazy="selectin"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def role_names(self) -> list[str]:
        return [r.name for r in self.roles]

    @property
    def permission_codes(self) -> list[str]:
        perms = {p.code for r in self.roles for p in r.permissions}
        if self.is_superuser:
            perms.add("*")
        return sorted(perms)


class RefreshToken(BaseEntity):
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")
