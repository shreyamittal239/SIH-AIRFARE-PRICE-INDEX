"""add booking_window_weights and dgca_fare_benchmark

Revision ID: edd2c50ee183
Revises: fcdee2f571c1
Create Date: 2026-09-12 15:56:48.887683

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'edd2c50ee183'
down_revision: Union[str, Sequence[str], None] = 'fcdee2f571c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create booking_window_weights table
    op.create_table(
        'booking_window_weights',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('window_id', sa.Integer(), nullable=False),
        sa.Column('weight', sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column('source_note', sa.String(length=150), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.ForeignKeyConstraint(['window_id'], ['booking_windows.window_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('weight >= 0 AND weight <= 1', name='ck_window_weight_range'),
    )
    # Partial unique index for active weight
    op.create_index(
        'uq_active_window_weight',
        'booking_window_weights',
        ['window_id'],
        unique=True,
        postgresql_where=sa.text('is_active = true'),
    )

    # 2. Create dgca_fare_benchmark table
    op.create_table(
        'dgca_fare_benchmark',
        sa.Column('benchmark_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('route_id', sa.Integer(), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('dgca_avg_fare', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('report_source', sa.String(length=150), nullable=True),
        sa.ForeignKeyConstraint(['route_id'], ['routes.route_id'], ),
        sa.PrimaryKeyConstraint('benchmark_id'),
        sa.UniqueConstraint('route_id', 'period_start', 'period_end', name='uq_dgca_fare_benchmark_route_period'),
    )
    op.create_index(
        'idx_dgca_fare_dates',
        'dgca_fare_benchmark',
        ['period_start', 'period_end'],
    )


def downgrade() -> None:
    op.drop_table('dgca_fare_benchmark')
    op.drop_table('booking_window_weights')
