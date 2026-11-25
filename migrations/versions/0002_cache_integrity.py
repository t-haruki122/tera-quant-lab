"""Normalize cache symbols, keep newest rows, and enforce cache identities."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

KEYS = {
    "stock_prices": ["symbol", "timestamp", "interval"],
    # Financial history is not cached: this table stores the current snapshot.
    "financials": ["symbol"],
    "news": ["symbol", "url"],
    "company_profiles": ["symbol"],
    "indicators": ["symbol"],
}


def upgrade():
    op.add_column("stock_prices", sa.Column("interval", sa.String(20), server_default="1d", nullable=False))
    op.execute("UPDATE stock_prices SET interval = 'quote' WHERE open IS NULL")
    for table, columns in KEYS.items():
        op.execute(sa.text(f'UPDATE "{table}" SET symbol = upper(symbol)'))
        partition = ", ".join(f'"{column}"' for column in columns)
        op.execute(sa.text(f'''
            DELETE FROM "{table}" WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY {partition} ORDER BY cached_at DESC, id DESC
                    ) AS rank FROM "{table}"
                ) WHERE rank > 1
            )
        '''))
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.create_unique_constraint(f"uq_{table}_cache", columns)


def downgrade():
    for table in reversed(KEYS):
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint(f"uq_{table}_cache", type_="unique")
    with op.batch_alter_table("stock_prices") as batch:
        batch.drop_column("interval")
