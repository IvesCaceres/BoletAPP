"""Support one-off manual batches with detached receiver snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0013_manual_batches"
down_revision = "0012_add_sii_retention_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bhe_batches", sa.Column("source", sa.String(length=24), nullable=False, server_default="monthly"))
    op.create_index("ix_bhe_batches_source", "bhe_batches", ["source"])
    with op.batch_alter_table("bhe_batch_items") as batch_op:
        batch_op.alter_column("client_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("giro", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("email", sa.String(length=254), nullable=True))
        batch_op.add_column(sa.Column("fecha_emision", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bhe_batch_items") as batch_op:
        batch_op.drop_column("fecha_emision")
        batch_op.drop_column("email")
        batch_op.drop_column("giro")
        batch_op.alter_column("client_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_bhe_batches_source", table_name="bhe_batches")
    op.drop_column("bhe_batches", "source")
