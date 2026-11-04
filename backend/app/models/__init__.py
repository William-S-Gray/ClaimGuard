"""Model registry — import all models so Alembic/metadata sees them."""
from app.models.audit import AuditLog
from app.models.base import Base, BaseEntity
from app.models.claim import (
    Claim,
    ClaimFlag,
    ClaimItem,
    ShapContribution,
    TimelineEvent,
)
from app.models.investigation import Investigation, InvestigationComment
from app.models.member import Member
from app.models.notification import Notification
from app.models.provider import Provider, ProviderAppeal, TrustScoreSnapshot
from app.models.user import (
    Permission,
    RefreshToken,
    Role,
    User,
    role_permissions,
    user_roles,
)

__all__ = [
    "Base",
    "BaseEntity",
    "AuditLog",
    "Claim",
    "ClaimItem",
    "ClaimFlag",
    "ShapContribution",
    "TimelineEvent",
    "Investigation",
    "InvestigationComment",
    "Member",
    "Notification",
    "Provider",
    "ProviderAppeal",
    "TrustScoreSnapshot",
    "User",
    "Role",
    "Permission",
    "RefreshToken",
    "user_roles",
    "role_permissions",
]
