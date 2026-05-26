"""add social_post_history table

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
Create Date: 2026-03-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'e7f8a9b0c1d2'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'social_post_history' in inspector.get_table_names():
        return
    op.create_table(
        'social_post_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('query', sa.String(length=512), nullable=False),
        sa.Column('headline', sa.Text(), nullable=False),
        sa.Column('settings_snapshot', sa.JSON(), nullable=True),
        sa.Column('news_results', sa.JSON(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_social_post_history_id'),
        'social_post_history',
        ['id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_social_post_history_id'), table_name='social_post_history')
    op.drop_table('social_post_history')
