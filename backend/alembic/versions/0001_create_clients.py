"""create clients

Revision ID: 0001_create_clients
Revises:
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_create_clients"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rut", sa.String(length=20), nullable=False),
        sa.Column("razon_social", sa.String(length=100), nullable=False),
        sa.Column("giro", sa.String(length=80), nullable=True),
        sa.Column("direccion", sa.String(length=80), nullable=False),
        sa.Column("region", sa.Integer(), nullable=False),
        sa.Column("comuna", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("detalle_default", sa.JSON(), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rut", name="uq_clients_rut"),
    )
    op.create_index("ix_clients_rut", "clients", ["rut"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_clients_rut", table_name="clients")
    op.drop_table("clients")
