"""expand LLM providers + fallback_order

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
"""

from alembic import op
import sqlalchemy as sa


revision = "j4k5l6m7n8o9"
down_revision = "i3j4k5l6m7n8"
branch_labels = None
depends_on = None

COLUMNS = [
    ("gemini_api_key", sa.Text()),
    ("cloudflare_api_token", sa.Text()),
    ("cloudflare_account_id", sa.Text()),
    ("mistral_api_key", sa.Text()),
    ("cohere_api_key", sa.Text()),
    ("nvidia_api_key", sa.Text()),
    ("fallback_order", sa.JSON()),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_llm_settings" not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    for name, col_type in COLUMNS:
        if name not in columns:
            op.add_column("user_llm_settings", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_llm_settings" not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    for name, _ in reversed(COLUMNS):
        if name == "gemini_api_key":
            continue  # owned by prior migration
        if name in columns:
            op.drop_column("user_llm_settings", name)
