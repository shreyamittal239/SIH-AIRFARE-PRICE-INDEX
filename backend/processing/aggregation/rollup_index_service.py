"""Stage 5: Index Rollup Service.

Computes weekly, monthly, and yearly averages of the national composite index.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Tuple
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from backend.app.db.models.index_daily import IndexDaily

logger = logging.getLogger(__name__)

class RollupIndexService:
    """Orchestrates Stage 5: Aggregating daily national indices into rollups."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def compute_rollups(self, base_period_code: str, period_type: str) -> int:
        """
        Calculate rollups (WEEKLY, MONTHLY, YEARLY) for the given period.

        Args:
            base_period_code: The base period to aggregate.
            period_type: One of 'WEEKLY', 'MONTHLY', 'YEARLY'.

        Returns:
            The number of rollup records created or updated.
        """
        if period_type not in ('WEEKLY', 'MONTHLY', 'YEARLY'):
            raise ValueError("period_type must be one of 'WEEKLY', 'MONTHLY', 'YEARLY'")

        # 1. Fetch all daily national composite indices for the period
        stmt = select(IndexDaily).where(
            IndexDaily.index_level == "NATIONAL_COMPOSITE",
            IndexDaily.period_type == "DAILY",
            IndexDaily.base_period_code == base_period_code,
            IndexDaily.route_id == None,
            IndexDaily.window_id == None
        ).order_by(IndexDaily.index_date)

        daily_records = self.db.scalars(stmt).all()

        if not daily_records:
            logger.info("No daily national indices found to roll up for period %s.", base_period_code)
            return 0

        # 2. Group by period
        groups: Dict[date, List[IndexDaily]] = {}
        for rec in daily_records:
            period_start = self._get_period_start(rec.index_date, period_type)
            if period_start not in groups:
                groups[period_start] = []
            groups[period_start].append(rec)

        processed_count = 0
        for period_start, records in groups.items():
            if self._aggregate_and_persist(period_start, base_period_code, period_type, records):
                processed_count += 1

        self.db.commit()
        logger.info("Stage 5 complete: %d %s indices computed for period %s.", processed_count, period_type, base_period_code)
        return processed_count

    def _get_period_start(self, dt: date, period_type: str) -> date:
        """Returns the start date of the period for a given date."""
        if period_type == 'WEEKLY':
            # ISO week starts on Monday
            return dt - timedelta(days=dt.weekday())
        elif period_type == 'MONTHLY':
            # First day of the month
            return date(dt.year, dt.month, 1)
        elif period_type == 'YEARLY':
            # First day of the year
            return date(dt.year, 1, 1)
        raise ValueError(f"Unsupported period_type: {period_type}")

    def _aggregate_and_persist(self, period_start: date, base_period_code: str, period_type: str, records: List[IndexDaily]) -> bool:
        """Calculates average and persists the rollup record idempotently."""
        # Calculation: Simple average of index_value, minimum of routes_included
        values = [rec.index_value for rec in records]
        routes_counts = [rec.routes_included for rec in records]

        avg_value = sum(values) / Decimal(len(values))
        avg_value = avg_value.quantize(Decimal("1.0000"))
        min_routes = min(routes_counts)

        # Idempotent persistence
        idx_stmt = select(IndexDaily).where(
            IndexDaily.index_date == period_start,
            IndexDaily.base_period_code == base_period_code,
            IndexDaily.index_level == "NATIONAL_COMPOSITE",
            IndexDaily.period_type == period_type,
            IndexDaily.route_id == None,
            IndexDaily.window_id == None,
        )

        self.db.expire_all()
        existing_idx = self.db.execute(idx_stmt).scalar_one_or_none()

        if existing_idx:
            existing_idx.index_value = avg_value
            existing_idx.price_relative = avg_value
            existing_idx.routes_included = min_routes
        else:
            new_idx = IndexDaily(
                index_date=period_start,
                base_period_code=base_period_code,
                index_level="NATIONAL_COMPOSITE",
                period_type=period_type,
                route_id=None,
                window_id=None,
                index_value=avg_value,
                price_relative=avg_value,
                formula_type="ROLLUP_AVG",
                routes_included=min_routes,
            )
            self.db.add(new_idx)

        return True
