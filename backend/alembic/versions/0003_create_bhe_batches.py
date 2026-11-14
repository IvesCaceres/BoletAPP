"""create monthly BHE batches

Revision ID: 0003_create_bhe_batches
Revises: 0002_create_bhe_emissions
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_create_bhe_batches"
down_revision: Union[str, None] = "0002_create_bhe_emissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bhe_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("fecha_emision", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'ready', 'processing', 'completed', 'completed_with_errors')",
            name="ck_bhe_batches_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bhe_batches_period", "bhe_batches", ["period"], unique=False)
    op.create_index("ix_bhe_batches_status", "bhe_batches", ["status"], unique=False)

    op.create_table(
        "bhe_batch_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("rut_receptor", sa.String(length=20), nullable=False),
        sa.Column("razon_social", sa.String(length=100), nullable=False),
        sa.Column("direccion", sa.String(length=80), nullable=False),
        sa.Column("region", sa.Integer(), nullable=False),
        sa.Column("comuna", sa.String(length=80), nullable=False),
        sa.Column("detalle", sa.JSON(), nullable=False),
        sa.Column("monto", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("bhe_emission_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'pending', 'success', 'failed')",
            name="ck_bhe_batch_items_status",
        ),
        sa.CheckConstraint("monto > 0", name="ck_bhe_batch_items_monto_positive"),
        sa.ForeignKeyConstraint(["batch_id"], ["bhe_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bhe_emission_id"], ["bhe_emissions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "client_id", name="uq_bhe_batch_items_batch_client"),
    )
    op.create_index("ix_bhe_batch_items_batch_id", "bhe_batch_items", ["batch_id"], unique=False)
    op.create_index("ix_bhe_batch_items_client_id", "bhe_batch_items", ["client_id"], unique=False)
    op.create_index("ix_bhe_batch_items_status", "bhe_batch_items", ["status"], unique=False)
    op.create_index("ix_bhe_batch_items_bhe_emission_id", "bhe_batch_items", ["bhe_emission_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_bhe_batch_items_bhe_emission_id", table_name="bhe_batch_items")
    op.drop_index("ix_bhe_batch_items_status", table_name="bhe_batch_items")
    op.drop_index("ix_bhe_batch_items_client_id", table_name="bhe_batch_items")
    op.drop_index("ix_bhe_batch_items_batch_id", table_name="bhe_batch_items")
    op.drop_table("bhe_batch_items")
    op.drop_index("ix_bhe_batches_status", table_name="bhe_batches")
    op.drop_index("ix_bhe_batches_period", table_name="bhe_batches")
    op.drop_table("bhe_batches")
