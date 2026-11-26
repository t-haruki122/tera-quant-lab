"""Store user-owned backtest results."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.CheckConstraint("kind IN ('single', 'grid', 'portfolio', 'walkforward')", name="ck_backtest_run_kind"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_backtest_runs_user_id", "backtest_runs", ["user_id"])


def downgrade():
    op.drop_table("backtest_runs")
