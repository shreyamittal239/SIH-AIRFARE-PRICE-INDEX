"""Stage 2: Price Relative Calculation Service.

Computes the price relative for each route, booking window, and day by comparing
the representative fare against a base period benchmark.
"""

import logging
from decimal import Decimal
from typing import List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models.base_period_fare import BasePeriodFare
from backend.app.db.models.index_daily import IndexDaily
from backend.app.db.models.route_daily_summary import RouteDailySummary

logger = logging.getLogger(__name__)

class PriceRelativeService:
    """Orchestrates Stage 2: Computing price relatives for route-window cohorts."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def compute_relatives(self, base_period_code: str) -> int:
        """
        Calculate price relatives for all route daily summaries relative to
         the provided base period.

        Logic:
        1. Fetch all RouteDailySummary records.
        2. For each, find the corresponding BasePeriodFare for (route_id, window_id, base_period_code).
        3. Compute price_relative = (representative_fare / benchmark_fare) * 100.
        4. Persist to index_daily.

        Returns:
            The number of index records created or updated.
        """
        # Fetch all summaries to process
        stmt = select(RouteDailySummary)
        summaries = self.db.scalars(stmt).all()

        if not summaries:
            logger.info("No route daily summaries found for relative calculation.")
            return 0

        # Optimization: Fetch all applicable base period fares into a lookup map
        # Map: (route_id, window_id) -> benchmark_fare
        base_stmt = select(
            BasePeriodFare.route_id,
            BasePeriodFare.window_id,
            BasePeriodFare.benchmark_fare
        ).where(BasePeriodFare.base_period_code == base_period_code)

        base_rows = self.db.execute(base_stmt).all()
        base_lookup = {(r.route_id, r.window_id): r.benchmark_fare for r in base_rows}

        processed_count = 0

        for summ in summaries:
            key = (summ.route_id, summ.window_id)
            benchmark_fare = base_lookup.get(key)

            if benchmark_fare is None:
                logger.warning(
                    "Missing base period fare for route %d, window %d in period %s. Skipping.",
                    summ.route_id, summ.window_id, base_period_code
                )
                continue

            if benchmark_fare <= 0:
                logger.error(
                    "Invalid benchmark fare (<= 0) for route %d, window %d. Skipping.",
                    summ.route_id, summ.window_id
                )
                continue

            # Formula: (Representative Fare / Benchmark Fare) * 100
            # Using Decimal for precision
            price_relative = (summ.representative_fare / benchmark_fare) * Decimal("100")
            price_relative = price_relative.quantize(Decimal("1.000000")) # Match model Numeric(10, 6)

            # Idempotent persistence to index_daily
            # Use a lookup for existing records to avoid duplicates
            idx_stmt = select(IndexDaily).where(
                IndexDaily.index_date == summ.observation_date,
                IndexDaily.base_period_code == base_period_code,
                IndexDaily.index_level == "WINDOW_COMPOSITE",
                IndexDaily.route_id == summ.route_id,
                IndexDaily.window_id == summ.window_id,
                IndexDaily.formula_type == "JEVONS",
            )
            existing_idx = self.db.scalar(idx_stmt)

            if existing_idx:
                existing_idx.index_value = price_relative
                existing_idx.price_relative = price_relative
            else:
                new_idx = IndexDaily(
                    index_date=summ.observation_date,
                    base_period_code=base_period_code,
                    index_level="WINDOW_COMPOSITE",
                    route_id=summ.route_id,
                    window_id=summ.window_id,
                    index_value=price_relative,
                    price_relative=price_relative,
                    formula_type="JEVONS",
                    routes_included=1,
                )
                self.db.add(new_idx)

            processed_count += 1

        self.db.commit()
        logger.info("Stage 2 complete: %d price relatives computed for period %s.", processed_count, base_period_code)
        return processed_count
