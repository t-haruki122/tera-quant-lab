"""Version user tokens and track new watchlist item timestamps."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # Never reissue the subject of a deleted account on SQLite.
    conn = op.get_bind()
    sqlite = conn.dialect.name == "sqlite"
    has_autoincrement = sqlite and conn.scalar(sa.text(
        "SELECT instr(upper(sql), 'AUTOINCREMENT') FROM sqlite_master WHERE type='table' AND name='users'"
    ))
    recreate = sqlite and not has_autoincrement
    options = {"sqlite_autoincrement": True} if recreate else {}
    with op.batch_alter_table("users", recreate="always" if recreate else "never", table_kwargs=options) as batch:
        batch.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("user_list_items") as batch:
        batch.add_column(sa.Column("added_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("user_list_items") as batch:
        batch.drop_column("added_at")
    # Keep the SQLite ID high-water mark through a downgrade and re-upgrade.
    with op.batch_alter_table("users", recreate="never") as batch:
        batch.drop_column("token_version")
