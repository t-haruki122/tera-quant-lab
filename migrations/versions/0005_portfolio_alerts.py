"""Store portfolios, price alerts and optional metadata for cached quotes."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "portfolio_holdings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("avg_cost", sa.Float(), nullable=False),
        sa.Column("acquired_on", sa.Date()),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "symbol", name="uq_portfolio_user_symbol"),
        sa.CheckConstraint("shares > 0", name="ck_portfolio_shares"),
        sa.CheckConstraint("avg_cost >= 0", name="ck_portfolio_avg_cost"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "price_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime()),
        sa.Column("last_triggered_market_date", sa.Date()),
        sa.Column("trigger_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("armed", sa.Boolean(), nullable=False, server_default="1"),
        sa.CheckConstraint("kind IN ('above', 'below', 'change_pct')", name="ck_price_alert_kind"),
        sa.CheckConstraint("threshold > 0", name="ck_price_alert_threshold"),
        sa.CheckConstraint("trigger_count >= 0", name="ck_price_alert_trigger_count"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_price_alerts_user_id", "price_alerts", ["user_id"])
    with op.batch_alter_table("stock_prices") as batch:
        batch.add_column(sa.Column("quote_name", sa.String(200)))
        batch.add_column(sa.Column("quote_currency", sa.String(10)))
        batch.add_column(sa.Column("quote_day_change", sa.Float()))
        batch.add_column(sa.Column("quote_day_change_pct", sa.Float()))
        batch.add_column(sa.Column("quote_market_time", sa.String(32)))
        batch.add_column(sa.Column("quote_market_date", sa.String(10)))


def downgrade():
    with op.batch_alter_table("stock_prices", recreate="never") as batch:
        batch.drop_column("quote_market_date")
        batch.drop_column("quote_market_time")
        batch.drop_column("quote_day_change_pct")
        batch.drop_column("quote_day_change")
        batch.drop_column("quote_currency")
        batch.drop_column("quote_name")
    op.drop_table("price_alerts")
    op.drop_table("portfolio_holdings")
