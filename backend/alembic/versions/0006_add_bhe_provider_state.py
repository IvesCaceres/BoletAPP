"""add provider and SII preview state to BHE history"""
from alembic import op
import sqlalchemy as sa


revision = "0006_add_bhe_provider_state"
down_revision = "0005_add_client_fantasy_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bhe_emissions") as batch:
        batch.drop_constraint("ck_bhe_emissions_status", type_="check")
        batch.create_check_constraint(
            "ck_bhe_emissions_status",
            "status IN ('pending', 'success', 'failed', 'awaiting_final_confirmation')",
        )
        batch.alter_column("status", existing_type=sa.String(length=16), type_=sa.String(length=32))
        batch.add_column(
            sa.Column("provider", sa.String(length=20), nullable=False, server_default="simplefactura")
        )
        batch.add_column(sa.Column("provider_message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("sii_resolved_recipient_name", sa.String(length=150), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bhe_emissions") as batch:
        batch.drop_column("sii_resolved_recipient_name")
        batch.drop_column("provider_message")
        batch.drop_column("provider")
        batch.drop_constraint("ck_bhe_emissions_status", type_="check")
        batch.create_check_constraint(
            "ck_bhe_emissions_status",
            "status IN ('pending', 'success', 'failed')",
        )
        batch.alter_column("status", existing_type=sa.String(length=32), type_=sa.String(length=16))
