"""Persist batch provider and irreversible-click safety marker."""
from alembic import op
import sqlalchemy as sa

revision = "0010_add_batch_provider_and_click_safety"
down_revision = "0009_add_batch_retention_inheritance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bhe_batches") as batch:
        batch.add_column(sa.Column("provider", sa.String(length=20), nullable=False, server_default="simplefactura"))
        batch.create_index("ix_bhe_batches_provider", ["provider"], unique=False)
    with op.batch_alter_table("bhe_batch_items") as item:
        item.drop_constraint("ck_bhe_batch_items_status", type_="check")
        item.create_check_constraint(
            "ck_bhe_batch_items_status",
            "status IN ('draft', 'pending', 'success', 'failed', 'emission_status_unknown')",
        )
        item.alter_column("status", existing_type=sa.String(length=16), type_=sa.String(length=32), existing_nullable=False)
        item.add_column(sa.Column("final_click_occurred", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("bhe_batch_items") as item:
        item.drop_constraint("ck_bhe_batch_items_status", type_="check")
        item.create_check_constraint(
            "ck_bhe_batch_items_status",
            "status IN ('draft', 'pending', 'success', 'failed')",
        )
        item.alter_column("status", existing_type=sa.String(length=32), type_=sa.String(length=16), existing_nullable=False)
        item.drop_column("final_click_occurred")
    with op.batch_alter_table("bhe_batches") as batch:
        batch.drop_index("ix_bhe_batches_provider")
        batch.drop_column("provider")
