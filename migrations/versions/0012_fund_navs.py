"""Store the NAV observed for each fund and base date."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fund_navs",
        sa.Column("association_code", sa.String(8), primary_key=True),
        sa.Column("base_date", sa.Date(), primary_key=True),
        sa.Column("nav", sa.Float(), nullable=False),
        sa.Column("cancellation_price", sa.Float()),
        sa.Column("net_assets", sa.BigInteger()),
        sa.Column("change", sa.Float()),
        sa.Column("change_pct", sa.Float()),
        sa.Column("fund_name", sa.Text(), nullable=False),
        sa.Column("fund_cd", sa.String(16)),
        sa.Column("isin", sa.String(12)),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("fund_navs")
