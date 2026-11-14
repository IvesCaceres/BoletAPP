"""Associate batch emission attempts with their originating item."""
from alembic import op
import sqlalchemy as sa

revision = "0011_add_batch_item_to_bhe_emissions"
down_revision = "0010_add_batch_provider_and_click_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bhe_emissions") as emission:
        emission.add_column(sa.Column("batch_item_id", sa.Integer(), nullable=True))
        emission.create_index("ix_bhe_emissions_batch_item_id", ["batch_item_id"], unique=False)
        emission.create_foreign_key(
            "fk_bhe_emissions_batch_item_id_bhe_batch_items",
            "bhe_batch_items",
            ["batch_item_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("bhe_emissions") as emission:
        emission.drop_constraint("fk_bhe_emissions_batch_item_id_bhe_batch_items", type_="foreignkey")
        emission.drop_index("ix_bhe_emissions_batch_item_id")
        emission.drop_column("batch_item_id")
