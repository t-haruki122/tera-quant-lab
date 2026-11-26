"""Persist UTC daily API statistics."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_stats_daily",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("total_ms", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("date", "endpoint"),
    )


def downgrade():
    op.drop_table("api_stats_daily")
