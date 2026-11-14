"""create BHE emission history

Revision ID: 0002_create_bhe_emissions
Revises: 0001_create_clients
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_create_bhe_emissions"
down_revision: Union[str, None] = "0001_create_clients"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bhe_emissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("rut_receptor", sa.String(length=20), nullable=False),
        sa.Column("razon_social", sa.String(length=100), nullable=False),
        sa.Column("direccion", sa.String(length=80), nullable=False),
        sa.Column("region", sa.Integer(), nullable=False),
        sa.Column("comuna", sa.String(length=80), nullable=False),
        sa.Column("detalle", sa.JSON(), nullable=False),
        sa.Column("monto_total", sa.Integer(), nullable=False),
        sa.Column("fecha_emision_solicitada", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_status", sa.Integer(), nullable=True),
        sa.Column("folio", sa.Integer(), nullable=True),
        sa.Column("codigo_barras", sa.String(length=500), nullable=True),
        sa.Column("fecha_emision_provider", sa.String(length=40), nullable=True),
        sa.Column("pdf_base64", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('pending', 'success', 'failed')",
            name="ck_bhe_emissions_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bhe_emissions_client_id", "bhe_emissions", ["client_id"], unique=False)
    op.create_index("ix_bhe_emissions_rut_receptor", "bhe_emissions", ["rut_receptor"], unique=False)
    op.create_index("ix_bhe_emissions_status", "bhe_emissions", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_bhe_emissions_status", table_name="bhe_emissions")
    op.drop_index("ix_bhe_emissions_rut_receptor", table_name="bhe_emissions")
    op.drop_index("ix_bhe_emissions_client_id", table_name="bhe_emissions")
    op.drop_table("bhe_emissions")
