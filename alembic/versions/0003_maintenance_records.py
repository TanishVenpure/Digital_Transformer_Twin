"""Add supplied maintenance history records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transformer_id", sa.String(length=255), nullable=False),
        sa.Column("record_type", sa.String(length=80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maintenance_records_transformer_id", "maintenance_records", ["transformer_id"])
    op.create_index("ix_maintenance_records_record_type", "maintenance_records", ["record_type"])
    op.create_index("ix_maintenance_records_occurred_at", "maintenance_records", ["occurred_at"])
    op.create_index("ix_maintenance_transformer_occurred", "maintenance_records", ["transformer_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_transformer_occurred", table_name="maintenance_records")
    op.drop_index("ix_maintenance_records_occurred_at", table_name="maintenance_records")
    op.drop_index("ix_maintenance_records_record_type", table_name="maintenance_records")
    op.drop_index("ix_maintenance_records_transformer_id", table_name="maintenance_records")
    op.drop_table("maintenance_records")
