"""Persist sanitized SII retention gate diagnostics per emission."""
from alembic import op
import sqlalchemy as sa


revision = "0012_add_sii_retention_diagnostics"
down_revision = "0011_add_batch_item_to_bhe_emissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bhe_emissions",
        sa.Column("sii_retention_diagnostics", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bhe_emissions", "sii_retention_diagnostics")
