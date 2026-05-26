"""add_category_to_trending_keywords

Revision ID: c16cad57fdc0
Revises: 5f4a99009e10
Create Date: 2026-01-13 19:31:46.553255

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c16cad57fdc0'
down_revision = '5f4a99009e10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add category column to trending_keywords table
    op.add_column('trending_keywords', sa.Column('category', sa.String(), nullable=True))
    op.create_index(op.f('ix_trending_keywords_category'), 'trending_keywords', ['category'], unique=False)


def downgrade() -> None:
    # Remove category column and index
    op.drop_index(op.f('ix_trending_keywords_category'), table_name='trending_keywords')
    op.drop_column('trending_keywords', 'category')

