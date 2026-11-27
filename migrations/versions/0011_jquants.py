"""Store J-Quants issue master, disclosure summaries, and sync state."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jq_issues",
        sa.Column("code5", sa.String(5), primary_key=True),
        sa.Column("symbol", sa.String(20)),
        sa.Column("name", sa.Text()),
        sa.Column("name_en", sa.Text()),
        sa.Column("sector17_code", sa.Text()),
        sa.Column("sector17_name", sa.Text()),
        sa.Column("sector33_code", sa.Text()),
        sa.Column("sector33_name", sa.Text()),
        sa.Column("scale_category", sa.Text()),
        sa.Column("market_code", sa.Text()),
        sa.Column("market_name", sa.Text()),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "jq_fin_summaries",
        sa.Column("disc_no", sa.String(32), primary_key=True),
        sa.Column("code5", sa.String(5), nullable=False),
        sa.Column("symbol", sa.String(20)),
        sa.Column("disclosed_date", sa.Date(), nullable=False),
        sa.Column("disclosed_time", sa.Time()),
        sa.Column("doc_type", sa.Text()),
        sa.Column("period_type", sa.String(8)),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("fy_start", sa.Date()),
        sa.Column("fy_end", sa.Date()),
        sa.Column("sales", sa.Float()),
        sa.Column("operating_profit", sa.Float()),
        sa.Column("ordinary_profit", sa.Float()),
        sa.Column("net_profit", sa.Float()),
        sa.Column("eps", sa.Float()),
        sa.Column("diluted_eps", sa.Float()),
        sa.Column("total_assets", sa.Float()),
        sa.Column("equity", sa.Float()),
        sa.Column("equity_ratio", sa.Float()),
        sa.Column("bps", sa.Float()),
        sa.Column("shareholders_equity", sa.Float()),
        sa.Column("roe", sa.Float()),
        sa.Column("cfo", sa.Float()),
        sa.Column("cfi", sa.Float()),
        sa.Column("cff", sa.Float()),
        sa.Column("cash", sa.Float()),
        sa.Column("div_annual", sa.Float()),
        sa.Column("payout_ratio_annual", sa.Float()),
        sa.Column("forecast_sales", sa.Float()),
        sa.Column("forecast_operating_profit", sa.Float()),
        sa.Column("forecast_ordinary_profit", sa.Float()),
        sa.Column("forecast_net_profit", sa.Float()),
        sa.Column("forecast_eps", sa.Float()),
        sa.Column("forecast_div_annual", sa.Float()),
        sa.Column("next_forecast_sales", sa.Float()),
        sa.Column("next_forecast_operating_profit", sa.Float()),
        sa.Column("next_forecast_ordinary_profit", sa.Float()),
        sa.Column("next_forecast_net_profit", sa.Float()),
        sa.Column("next_forecast_eps", sa.Float()),
        sa.Column("next_forecast_div_annual", sa.Float()),
        sa.Column("shares_outstanding_fy", sa.BigInteger()),
        sa.Column("treasury_shares_fy", sa.BigInteger()),
        sa.Column("average_shares", sa.BigInteger()),
        sa.Column("consolidated", sa.Boolean()),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_jq_fin_summaries_code5_period_end", "jq_fin_summaries", ["code5", "period_end"])
    op.create_index("ix_jq_fin_summaries_symbol_disclosed_date", "jq_fin_summaries", ["symbol", "disclosed_date"])
    op.create_table(
        "jq_sync_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade():
    for table in ("jq_sync_state", "jq_fin_summaries", "jq_issues"):
        op.drop_table(table)
