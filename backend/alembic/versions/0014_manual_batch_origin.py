"""Record the source batch for editable copies of failed manual batches."""
from alembic import op
import sqlalchemy as sa

revision = "0014_manual_batch_origin"
down_revision = "0013_manual_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bhe_batches", sa.Column("source_batch_id", sa.Integer(), nullable=True))
    op.create_index("ix_bhe_batches_source_batch_id", "bhe_batches", ["source_batch_id"])
    with op.batch_alter_table("bhe_batches") as batch_op:
        batch_op.create_foreign_key("fk_bhe_batches_source_batch_id", "bhe_batches", ["source_batch_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("bhe_batches") as batch_op:
        batch_op.drop_constraint("fk_bhe_batches_source_batch_id", type_="foreignkey")
    op.drop_index("ix_bhe_batches_source_batch_id", table_name="bhe_batches")
    op.drop_column("bhe_batches", "source_batch_id")
