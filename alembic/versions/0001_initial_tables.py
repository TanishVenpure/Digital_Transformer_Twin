"""Baseline existing backend tables, before the first real maintenance rule.

The repository previously had no revisions. Existing unmanaged installations
must compare their schema to this baseline before stamping 0001.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reviewed baseline of the original backend tables.
    op.create_table('alerts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('transformer_id', sa.String(length=255), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('parameter', sa.String(length=255), nullable=False),
    sa.Column('formula_name', sa.String(length=255), nullable=False),
    sa.Column('formula_value', sa.Float(), nullable=False),
    sa.Column('threshold', sa.Float(), nullable=False),
    sa.Column('comparison_operator', sa.String(length=2), nullable=False),
    sa.Column('severity', sa.String(length=50), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('formula_inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('acknowledged', sa.Boolean(), nullable=False),
    sa.Column('resolved', sa.Boolean(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_alert_transformer_timestamp', 'alerts', ['transformer_id', 'timestamp'], unique=False)
    op.create_index(op.f('ix_alerts_resolved'), 'alerts', ['resolved'], unique=False)
    op.create_index(op.f('ix_alerts_severity'), 'alerts', ['severity'], unique=False)
    op.create_index(op.f('ix_alerts_timestamp'), 'alerts', ['timestamp'], unique=False)
    op.create_index(op.f('ix_alerts_transformer_id'), 'alerts', ['transformer_id'], unique=False)
    op.create_table('daily_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('transformer_id', sa.String(length=255), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('aggregated_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('formula_results', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('transformer_id', 'date', name='uq_daily_transformer_date')
    )
    op.create_index(op.f('ix_daily_metrics_date'), 'daily_metrics', ['date'], unique=False)
    op.create_index(op.f('ix_daily_metrics_transformer_id'), 'daily_metrics', ['transformer_id'], unique=False)
    op.create_index('ix_daily_transformer_date', 'daily_metrics', ['transformer_id', 'date'], unique=False)
    op.create_table('sensor_readings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('transformer_id', sa.String(length=255), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('raw_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sensor_readings_timestamp'), 'sensor_readings', ['timestamp'], unique=False)
    op.create_index(op.f('ix_sensor_readings_transformer_id'), 'sensor_readings', ['transformer_id'], unique=False)
    op.create_index('ix_sensor_transformer_timestamp', 'sensor_readings', ['transformer_id', 'timestamp'], unique=False)



def downgrade() -> None:
    # Reviewed baseline of the original backend tables.
    op.drop_index('ix_sensor_transformer_timestamp', table_name='sensor_readings')
    op.drop_index(op.f('ix_sensor_readings_transformer_id'), table_name='sensor_readings')
    op.drop_index(op.f('ix_sensor_readings_timestamp'), table_name='sensor_readings')
    op.drop_table('sensor_readings')
    op.drop_index('ix_daily_transformer_date', table_name='daily_metrics')
    op.drop_index(op.f('ix_daily_metrics_transformer_id'), table_name='daily_metrics')
    op.drop_index(op.f('ix_daily_metrics_date'), table_name='daily_metrics')
    op.drop_table('daily_metrics')
    op.drop_index(op.f('ix_alerts_transformer_id'), table_name='alerts')
    op.drop_index(op.f('ix_alerts_timestamp'), table_name='alerts')
    op.drop_index(op.f('ix_alerts_severity'), table_name='alerts')
    op.drop_index(op.f('ix_alerts_resolved'), table_name='alerts')
    op.drop_index('ix_alert_transformer_timestamp', table_name='alerts')
    op.drop_table('alerts')
