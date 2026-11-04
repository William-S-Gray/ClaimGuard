"""member satisfaction rating on claims

Revision ID: d3a9f1b7c2e4
Revises: c8e1d4f2a6b9
Create Date: 2026-11-04 18:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd3a9f1b7c2e4'
down_revision: str | None = 'c8e1d4f2a6b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('claims', sa.Column('member_rating', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('claims', 'member_rating')
