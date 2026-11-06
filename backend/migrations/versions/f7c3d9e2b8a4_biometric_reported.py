"""whether the claim source reported biometric verification at all

Revision ID: f7c3d9e2b8a4
Revises: e5b2c8d4f1a7
Create Date: 2026-11-07 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f7c3d9e2b8a4'
down_revision: str | None = 'e5b2c8d4f1a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing claims count as reported: we can't know otherwise, and flagging
    # history retroactively would only add noise.
    op.add_column('claims', sa.Column(
        'biometric_reported', sa.Boolean(), nullable=False, server_default=sa.true()
    ))


def downgrade() -> None:
    op.drop_column('claims', 'biometric_reported')
