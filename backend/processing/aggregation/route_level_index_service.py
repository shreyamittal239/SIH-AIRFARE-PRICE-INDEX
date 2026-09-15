"""Stage 3: Route-Level Index Calculation Service.

Computes the route-level daily index by aggregating price relatives across
available booking windows.
"""

import logging
from decimal import Decimal
from typing import List, Optional, Dict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models.index_daily import IndexDaily

logger = logging.getLogger(__name__)

def calculate_route_index(values: List[Decimal], weights: Optional[Dict[int, Decimal]] = None) -> Decimal:
    """
    Calculate the weighted or arithmetic mean of price relatives.

    Args:
        values: List of price relatives to aggregate.
        weights: Optional map of window_id to weight. If None, uses equal weights.

    Returns:
        The calculated index value.
    """
    if not values:
        raise ValueError("Cannot calculate route index for empty values list.")

    if weights is None:
        # Flat arithmetic mean: SUM(values) / COUNT(values)
        return sum(values) / Decimal(len(values))

    # Weighted mean implementation for future use:
    # route_index = SUM(value_i * weight_i) / SUM(weight_i)
    # This assumes weights are provided for the indices corresponding to the values.
    # Since this is a utility, we'll handle values and weights as pairs if weights provided.
    # Note: The calling service will need to pass them aligned.
    raise NotImplementedError("Weighted calculation logic requires aligned values/weights pairs.")

class RouteLevelIndexService:
    """Orchestrates Stage 3: Aggregating window composites into route-level indices."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def compute_route_indices(self, base_period_code: str) -> int:
        """
        Calculate ROUTE_LEVEL indices for all routes and dates.

        Logic:
        1. Query all index_daily rows where index_level = 'WINDOW_COMPOSITE'.
        2. Group by (index_date, route_id).
        3. For each group, calculate the arithmetic mean of price_relatives.
        4. Persist to index_daily as 'ROUTE_LEVEL'.

        Returns:
            The number of index records created or updated.
        """
        # Fetch only WINDOW_COMPOSITE records for the given period
        stmt = select(IndexDaily).where(
            IndexDaily.index_level == "WINDOW_COMPOSITE",
            IndexDaily.base_period_code == base_period_code
        )
        window_records = self.db.scalars(stmt).all()

        if not window_records:
            logger.info("No WINDOW_COMPOSITE records found for Stage 3.")
            return 0

        # Group by (date, route_id)
        # Map: (index_date, route_id) -> List[price_relative]
        cohort_map: Dict[tuple, List[Decimal]] = {}
        for rec in window_records:
            key = (rec.index_date, rec.route_id)
            if key not in cohort_map:
                cohort_map[key] = []
            cohort_map[key].append(rec.price_relative)

        processed_count = 0

        for (index_date, route_id), values in cohort_map.items():
            # Calculate arithmetic mean (weights=None)
            route_index_val = calculate_route_index(values)
            route_index_val = route_index_val.quantize(Decimal("1.0000"))

            # Idempotent persistence
            idx_stmt = select(IndexDaily).where(
                IndexDaily.index_date == index_date,
                IndexDaily.base_period_code == base_period_code,
                IndexDaily.index_level == "ROUTE_LEVEL",
                IndexDaily.route_id == route_id,
                IndexDaily.window_id == None, # ROUTE_LEVEL always has NULL window_id
                IndexDaily.formula_type == "JEVONS",
            )
            existing_idx = self.db.scalar(idx_stmt)

            if existing_idx:
                existing_idx.index_value = route_index_val
                existing_idx.price_relative = route_index_val
            else:
                new_idx = IndexDaily(
                    index_date=index_date,
                    base_period_code=base_period_code,
                    index_level="ROUTE_LEVEL",
                    route_id=route_id,
                    window_id=None,
                    index_value=route_index_val,
                    price_relative=route_index_val,
                    formula_type="JEVONS",
                    routes_included=1,
                )
                self.db.add(new_idx)

            processed_count += 1

        self.db.commit()
        logger.info("Stage 3 complete: %d ROUTE_LEVEL indices computed for period %s.", processed_count, base_period_code)
        return processed_count
