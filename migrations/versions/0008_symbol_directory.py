"""Add the local symbol directory."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "symbol_directory",
        sa.Column("symbol", sa.String(20), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("search_key", sa.String(300), nullable=False),
        sa.Column("market", sa.String(2), nullable=False),
        sa.Column("segment", sa.String(60)),
        sa.Column("sector", sa.String(100)),
        sa.Column("is_etf", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("market IN ('JP', 'US')", name="ck_symbol_directory_market"),
        sa.CheckConstraint("source IN ('jpx', 'nasdaqtrader')", name="ck_symbol_directory_source"),
    )
    op.create_index("ix_symbol_directory_market", "symbol_directory", ["market"])


def downgrade():
    op.drop_table("symbol_directory")
