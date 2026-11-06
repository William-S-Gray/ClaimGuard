"""when the latest member verification alert was sent

Revision ID: e5b2c8d4f1a7
Revises: d3a9f1b7c2e4
Create Date: 2026-11-06 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e5b2c8d4f1a7'
down_revision: str | None = 'd3a9f1b7c2e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'claims', sa.Column('member_alert_sent_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('claims', 'member_alert_sent_at')
