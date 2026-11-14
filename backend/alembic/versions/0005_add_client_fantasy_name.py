"""add optional fantasy name to clients"""
from alembic import op
import sqlalchemy as sa


revision = "0005_add_client_fantasy_name"
down_revision = "0004_add_batch_global_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("nombre_fantasia", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "nombre_fantasia")
