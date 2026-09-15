"""Stage 4: National Index Calculation Service.

Computes the national composite index by aggregating route-level indices
using configured route weights with dynamic renormalization for unavailable routes.
"""

import logging
from decimal import Decimal
from typing import List, Optional, Dict, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models.index_daily import IndexDaily
from backend.app.db.models.route_weight import RouteWeight

logger = logging.getLogger(__name__)

class NationalIndexService:
    """Orchestrates Stage 4: Aggregating route-level indices into a national composite."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def compute_national_indices(self, base_period_code: str) -> int:
        """
        Calculate NATIONAL_COMPOSITE indices for all available dates in a base period.

        Logic:
        1. Identify all dates that have at least one ROUTE_LEVEL index.
        2. For each date, compute the weighted average of available route indices.
        3. Renormalize weights based on the routes actually participating on that date.
        4. Persist result to index_daily as 'NATIONAL_COMPOSITE'.

        Returns:
            The number of national index records created or updated.
        """
        # 1. Identify target dates
        stmt = select(IndexDaily.index_date).where(
            IndexDaily.index_level == "ROUTE_LEVEL",
            IndexDaily.base_period_code == base_period_code
        ).distinct()

        target_dates = self.db.scalars(stmt).all()

        if not target_dates:
            logger.info("No ROUTE_LEVEL records found to aggregate into National Index.")
            return 0

        processed_count = 0
        for index_date in target_dates:
            # IMPORTANT: Commit or flush to ensure existing record lookup works
            # in subsequent calls within the same session in some DB configurations.
            self.db.flush()
            if self._compute_for_date(index_date, base_period_code):
                processed_count += 1

        self.db.commit()
        logger.info("Stage 4 complete: %d NATIONAL_COMPOSITE indices computed for period %s.", processed_count, base_period_code)
        return processed_count

    def _compute_for_date(self, index_date, base_period_code: str) -> bool:
        """Computes and persists the national index for a single date."""

        # 1. Fetch ROUTE_LEVEL indices for this date
        stmt = select(IndexDaily).where(
            IndexDaily.index_level == "ROUTE_LEVEL",
            IndexDaily.index_date == index_date,
            IndexDaily.base_period_code == base_period_code
        )
        route_records = self.db.scalars(stmt).all()

        if not route_records:
            return False

        # Map: route_id -> index_value
        route_map = {rec.route_id: rec.index_value for rec in route_records}

        # 2. Fetch active route weights for this base period
        weight_stmt = select(RouteWeight).where(
            RouteWeight.base_period_code == base_period_code,
            RouteWeight.is_active == True
        )
        all_weights = self.db.scalars(weight_stmt).all()

        # 3. Filter participating routes (intersection of indices and weights)
        participating_data: List[Tuple[Decimal, Decimal]] = []
        for rw in all_weights:
            if rw.route_id in route_map:
                participating_data.append((route_map[rw.route_id], rw.weight))

        if not participating_data:
            logger.warning("No weight-matched routes found for date %s. Skipping.", index_date)
            return False

        # 4. Renormalization and Calculation
        # available_weight_sum = SUM(configured weights of participating routes)
        sum_weights = sum(weight for _, weight in participating_data)

        if sum_weights == 0:
            logger.warning("Sum of participating weights is zero for date %s. Skipping.", index_date)
            return False

        # National Index = SUM(route_index * (weight / sum_weights))
        national_index_val = sum(
            (index_val * weight) / sum_weights
            for index_val, weight in participating_data
        )

        # Precision: Match index_daily.index_value Numeric(10, 4)
        national_index_val = national_index_val.quantize(Decimal("1.0000"))

        # 5. Idempotent Persistence
        # To avoid session cache issues in tests, use a fresh query
        idx_stmt = select(IndexDaily).where(
            IndexDaily.index_level == "NATIONAL_COMPOSITE",
            IndexDaily.index_date == index_date,
            IndexDaily.base_period_code == base_period_code,
            IndexDaily.route_id == None,
            IndexDaily.window_id == None,
        )

        # Force fresh read from DB
        self.db.expire_all()
        existing_idx = self.db.execute(idx_stmt).scalar_one_or_none()

        if existing_idx:
            existing_idx.index_value = national_index_val
            existing_idx.price_relative = national_index_val
            existing_idx.routes_included = len(participating_data)
        else:
            new_idx = IndexDaily(
                index_date=index_date,
                base_period_code=base_period_code,
                index_level="NATIONAL_COMPOSITE",
                route_id=None,
                window_id=None,
                index_value=national_index_val,
                price_relative=national_index_val,
                formula_type="JEVONS",
                routes_included=len(participating_data),
            )
            self.db.add(new_idx)

        return True
