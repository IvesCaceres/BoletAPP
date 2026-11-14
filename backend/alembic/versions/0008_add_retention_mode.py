"""Persist semantic PPM retention mode for new BHE emissions."""
from alembic import op
import sqlalchemy as sa

revision = "0008_add_retention_mode"
down_revision = "0007_add_sii_finalization_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bhe_emissions") as batch:
        batch.add_column(sa.Column("retention_mode", sa.String(length=32), nullable=True))
        batch.create_check_constraint(
            "ck_bhe_emissions_retention_mode",
            "retention_mode IS NULL OR retention_mode IN ('receiver_retains_ppm', 'issuer_handles_ppm')",
        )


def downgrade() -> None:
    with op.batch_alter_table("bhe_emissions") as batch:
        batch.drop_constraint("ck_bhe_emissions_retention_mode", type_="check")
        batch.drop_column("retention_mode")
