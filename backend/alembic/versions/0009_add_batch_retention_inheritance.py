"""Add semantic batch retention and optional item overrides."""
from alembic import op
import sqlalchemy as sa

revision = "0009_add_batch_retention_inheritance"
down_revision = "0008_add_retention_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bhe_batches") as batch:
        batch.add_column(sa.Column("retention_mode", sa.String(length=32), nullable=True))
        batch.create_check_constraint(
            "ck_bhe_batches_retention_mode",
            "retention_mode IS NULL OR retention_mode IN ('receiver_retains_ppm', 'issuer_handles_ppm')",
        )
    with op.batch_alter_table("bhe_batch_items") as item:
        item.add_column(sa.Column("retention_mode_override", sa.String(length=32), nullable=True))
        item.create_check_constraint(
            "ck_bhe_batch_items_retention_mode_override",
            "retention_mode_override IS NULL OR retention_mode_override IN ('receiver_retains_ppm', 'issuer_handles_ppm')",
        )


def downgrade() -> None:
    with op.batch_alter_table("bhe_batch_items") as item:
        item.drop_constraint("ck_bhe_batch_items_retention_mode_override", type_="check")
        item.drop_column("retention_mode_override")
    with op.batch_alter_table("bhe_batches") as batch:
        batch.drop_constraint("ck_bhe_batches_retention_mode", type_="check")
        batch.drop_column("retention_mode")
