"""Pre-batch-4 schema; also adopt databases previously made by create_all."""

from alembic import op

from migrations.legacy_schema import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    Base.metadata.create_all(connection, checkfirst=True)
    # create_all skips indexes on tables that already exist.
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(connection, checkfirst=True)


def downgrade():
    Base.metadata.drop_all(op.get_bind(), checkfirst=True)
