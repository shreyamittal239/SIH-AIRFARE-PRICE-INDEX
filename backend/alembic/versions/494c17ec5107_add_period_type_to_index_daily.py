"""add period_type to index_daily

Revision ID: 494c17ec5107
Revises: edd2c50ee183
Create Date: 2026-09-15 14:03:54.627059

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '494c17ec5107'
down_revision: Union[str, Sequence[str], None] = 'edd2c50ee183'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add period_type column with default
    op.add_column('index_daily', sa.Column('period_type', sa.String(length=10), server_default='DAILY', nullable=False))

    # Add check constraint for period_type
    op.create_check_constraint(
        'ck_index_daily_period_type',
        'index_daily',
        "period_type IN ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY')"
    )

    # Update unique index to include period_type
    op.drop_index(op.f('uq_index_daily_coalesce'), table_name='index_daily')
    op.create_index(
        'uq_index_daily_coalesce',
        'index_daily',
        ['index_date', 'base_period_code', 'index_level', 'period_type', sa.literal_column('COALESCE(route_id, -1)'), sa.literal_column('COALESCE(window_id, -1)'), 'formula_type'],
        unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_index_daily_coalesce', table_name='index_daily')
    op.create_index(
        op.f('uq_index_daily_coalesce'),
        'index_daily',
        ['index_date', 'base_period_code', 'index_level', sa.literal_column("COALESCE(route_id, '-1'::integer)"), sa.literal_column("COALESCE(window_id::integer, '-1'::integer)"), 'formula_type'],
        unique=True
    )
    op.drop_constraint('ck_index_daily_period_type', 'index_daily', type_='check')
    op.drop_column('index_daily', 'period_type')
