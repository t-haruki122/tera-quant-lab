"""Add statement currency to the financials cache."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("financials", sa.Column("currency", sa.String(10)))


def downgrade():
    op.drop_column("financials", "currency")
