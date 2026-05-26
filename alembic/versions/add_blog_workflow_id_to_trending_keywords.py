"""add blog_workflow_id to trending_keywords

Revision ID: d5e6f7a8b9c0
Revises: b2c3d4e5f6a7
Create Date: 2026-02-21

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5e6f7a8b9c0'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'trending_keywords',
        sa.Column('blog_workflow_id', sa.Integer(), nullable=True)
    )
    op.create_index(
        op.f('ix_trending_keywords_blog_workflow_id'),
        'trending_keywords',
        ['blog_workflow_id'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_trending_keywords_blog_workflow_id'),
        table_name='trending_keywords'
    )
    op.drop_column('trending_keywords', 'blog_workflow_id')
