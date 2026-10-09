"""Add maintenance category and extensible rule diagnostics to existing alerts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy alerts remain unclassified; never invent their maintenance category.
    op.add_column("alerts", sa.Column("maintenance_type", sa.String(23), nullable=True))
    op.create_check_constraint(
        "maintenance_type", "alerts",
        "maintenance_type IN ('SYSTEM_MAINTENANCE', 'TRANSFORMER_MAINTENANCE')",
    )
    op.create_index("ix_alerts_maintenance_type", "alerts", ["maintenance_type"])
    op.add_column("alerts", sa.Column(
        "details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb"),
    ))


def downgrade() -> None:
    op.drop_column("alerts", "details")
    op.drop_index("ix_alerts_maintenance_type", table_name="alerts")
    op.drop_constraint("maintenance_type", "alerts", type_="check")
    op.drop_column("alerts", "maintenance_type")
