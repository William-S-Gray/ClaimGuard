"""provider portal: link users to a provider, and TrustScore appeals

Revision ID: c8e1d4f2a6b9
Revises: b2f4a7c9d1e3
Create Date: 2026-11-04 15:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c8e1d4f2a6b9'
down_revision: str | None = 'b2f4a7c9d1e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('provider_id', sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            'fk_users_provider_id', 'providers', ['provider_id'], ['id'], ondelete='SET NULL'
        )
        batch.create_index('ix_users_provider_id', ['provider_id'])

    op.create_table(
        'provider_appeals',
        sa.Column('provider_id', sa.Uuid(), nullable=False),
        sa.Column('submitted_by', sa.Uuid(), nullable=True),
        sa.Column('score_at_submission', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('outcome_notes', sa.Text(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_provider_appeals_provider_id', 'provider_appeals', ['provider_id'])
    op.create_index('ix_provider_appeals_status', 'provider_appeals', ['status'])
    op.create_index('ix_provider_appeals_deleted_at', 'provider_appeals', ['deleted_at'])


def downgrade() -> None:
    op.drop_table('provider_appeals')
    with op.batch_alter_table('users') as batch:
        batch.drop_index('ix_users_provider_id')
        batch.drop_constraint('fk_users_provider_id', type_='foreignkey')
        batch.drop_column('provider_id')
