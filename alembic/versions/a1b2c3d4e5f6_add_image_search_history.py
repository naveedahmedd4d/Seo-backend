"""add image_search_history table

Revision ID: a1b2c3d4e5f6
Revises: c16cad57fdc0
Create Date: 2026-02-02

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'c16cad57fdc0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('image_search_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('keyword', sa.String(), nullable=False),
        sa.Column('results', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_image_search_history_id'), 'image_search_history', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_image_search_history_id'), table_name='image_search_history')
    op.drop_table('image_search_history')
