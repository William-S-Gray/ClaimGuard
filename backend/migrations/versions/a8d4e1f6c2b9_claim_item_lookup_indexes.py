"""indexes for FraudShield's peer-shortfall item lookup

Revision ID: a8d4e1f6c2b9
Revises: f7c3d9e2b8a4
Create Date: 2026-11-07 09:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a8d4e1f6c2b9'
down_revision: str | None = 'f7c3d9e2b8a4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_claim_items_nappi_code', 'claim_items', ['nappi_code'])
    op.create_index(
        'ix_claim_items_description_norm', 'claim_items',
        [sa.text('lower(trim(description))')],
    )


def downgrade() -> None:
    op.drop_index('ix_claim_items_description_norm', table_name='claim_items')
    op.drop_index('ix_claim_items_nappi_code', table_name='claim_items')
