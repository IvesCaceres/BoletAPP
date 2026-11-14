"""add global detail to batches

Revision ID: 0004_add_batch_global_detail
Revises: 0003_create_bhe_batches
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_add_batch_global_detail"
down_revision = "0003_create_bhe_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bhe_batches", sa.Column("detalle_global", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("bhe_batches", "detalle_global")
