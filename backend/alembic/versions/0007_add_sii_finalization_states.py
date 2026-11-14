"""Allow the explicit SII final-confirmation state machine."""
from alembic import op

revision = "0007_add_sii_finalization_states"
down_revision = "0006_add_bhe_provider_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bhe_emissions") as batch:
        batch.drop_constraint("ck_bhe_emissions_status", type_="check")
        batch.create_check_constraint(
            "ck_bhe_emissions_status",
            "status IN ('pending', 'success', 'failed', 'awaiting_final_confirmation', 'finalizing', 'emission_status_unknown')",
        )


def downgrade() -> None:
    with op.batch_alter_table("bhe_emissions") as batch:
        batch.drop_constraint("ck_bhe_emissions_status", type_="check")
        batch.create_check_constraint(
            "ck_bhe_emissions_status",
            "status IN ('pending', 'success', 'failed', 'awaiting_final_confirmation')",
        )
