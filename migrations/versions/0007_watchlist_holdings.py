"""Move holdings into the default watchlist, discarding acquisition details."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_lists", sa.Column("cash_jpy", sa.Integer()))
    op.add_column("user_list_items", sa.Column("shares", sa.Float()))
    op.execute(sa.text("""
        INSERT INTO user_lists (user_id, name, created_at, updated_at)
        SELECT h.user_id, 'マイリスト', MIN(h.created_at), MAX(h.updated_at)
        FROM portfolio_holdings AS h
        WHERE NOT EXISTS (SELECT 1 FROM user_lists AS l WHERE l.user_id = h.user_id)
        GROUP BY h.user_id
    """))
    op.execute(sa.text("""
        WITH default_holdings AS (
            SELECT h.*, (SELECT l.id FROM user_lists AS l WHERE l.user_id = h.user_id
                         ORDER BY l.created_at, l.id LIMIT 1) AS list_id
            FROM portfolio_holdings AS h
        )
        INSERT INTO user_list_items (list_id, symbol, added_at, shares)
        SELECT h.list_id, COALESCE((
            SELECT i.symbol FROM user_list_items AS i
            WHERE i.list_id = h.list_id AND UPPER(i.symbol) = UPPER(h.symbol)
            ORDER BY (i.symbol = UPPER(i.symbol)) DESC, i.id LIMIT 1
        ), UPPER(h.symbol)), h.created_at, h.shares
        FROM default_holdings AS h
        WHERE 1
        ON CONFLICT (list_id, symbol) DO UPDATE
        SET symbol = UPPER(excluded.symbol), shares = COALESCE(user_list_items.shares, 0) + excluded.shares
    """))
    op.drop_table("portfolio_holdings")


def downgrade():
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
    with op.batch_alter_table("user_list_items", recreate="never") as batch:
        batch.drop_column("shares")
    with op.batch_alter_table("user_lists", recreate="never") as batch:
        batch.drop_column("cash_jpy")
