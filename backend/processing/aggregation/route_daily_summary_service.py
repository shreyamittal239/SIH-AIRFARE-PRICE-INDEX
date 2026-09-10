"""Route-Day-Booking-Window aggregation service.

Aggregates eligible, cleaned, and cross-source reconciled fare observations
into RouteDailySummary records and persists them idempotently into PostgreSQL.

Statistical Terminology Note:
This service computes summary statistics (min, median, arithmetic mean, and geometric mean)
of flight fare levels within each (observation_date, route_id, window_id) cohort.
The geometric mean of fare levels (geometric_mean_fare) is used as a representative-fare
statistic. This is distinct from a formal Jevons price index, which is constructed from
the geometric mean of price relatives (current_fare / base_fare) across distinct time periods.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route_daily_summary import RouteDailySummary
from backend.processing.aggregation.reconciliation import (
    CrossSourcePolicy,
    reconcile_cross_source_quotes,
)
from backend.processing.aggregation.statistics import (
    RepresentativeFareMetric,
    calculate_route_summary_stats,
)

logger = logging.getLogger(__name__)

# Standard SIH booking window advance days mapping
DEFAULT_WINDOW_ADVANCE_DAYS: Dict[int, int] = {
    1: 1,   # T+1
    2: 7,   # T+7
    3: 15,  # T+15
    4: 30,  # T+30
    5: 45,  # T+45
}


@dataclass
class AggregationResult:
    """Summary of aggregation service execution."""

    total_observations_evaluated: int
    eligible_observations_count: int
    cohorts_formed: int
    summaries_created: int
    summaries_updated: int
    summaries: List[RouteDailySummary]


class RouteDailySummaryService:
    """Orchestrates route-window cohort aggregation and idempotent persistence."""

    def __init__(
        self,
        db: Optional[Session] = None,
        cross_source_policy: CrossSourcePolicy = CrossSourcePolicy.MIN_VALID_FARE,
        exclude_outliers: bool = True,
        representative_metric: RepresentativeFareMetric = RepresentativeFareMetric.GEOMETRIC_MEAN,
        window_advance_days_map: Optional[Dict[int, int]] = None,
    ) -> None:
        """Initialize aggregation service parameters.

        Args:
            db: Optional SQLAlchemy database session for persistence.
            cross_source_policy: Cross-source reconciliation strategy.
            exclude_outliers: Whether to exclude observations tagged as OUTLIER.
            representative_metric: Metric selected for representative_fare column.
            window_advance_days_map: Optional explicit mapping of window_id to target_advance_days.
        """
        self.db = db
        self.cross_source_policy = cross_source_policy
        self.exclude_outliers = exclude_outliers
        self.representative_metric = representative_metric
        self.window_advance_days_map: Dict[int, int] = dict(DEFAULT_WINDOW_ADVANCE_DAYS)
        if window_advance_days_map:
            self.window_advance_days_map.update(window_advance_days_map)
        elif self.db is not None:
            self._load_window_advance_days_from_db()

    def _load_window_advance_days_from_db(self) -> None:
        """Query seeded booking windows from the database to populate target advance days."""
        try:
            from backend.app.db.models.booking_window import BookingWindow
            stmt = select(BookingWindow.window_id, BookingWindow.target_advance_days)
            rows = self.db.execute(stmt).all()
            for wid, adv in rows:
                if wid is not None and adv is not None:
                    self.window_advance_days_map[wid] = adv
        except Exception as exc:
            logger.debug("Could not query booking windows from db: %s", exc)

    def resolve_target_advance_days(self, obs: Any) -> Optional[int]:
        """Resolve expected target advance days for an observation.

        Priority:
        1. obs.booking_window.target_advance_days (if relational model attached)
        2. self.window_advance_days_map[window_id]
        3. obs.advance_days (if standard advance days value)
        """
        bw = getattr(obs, "booking_window", None)
        if bw is not None and hasattr(bw, "target_advance_days") and bw.target_advance_days is not None:
            return bw.target_advance_days

        wid = getattr(obs, "window_id", None) or getattr(obs, "booking_window_id", None)
        if wid is not None and wid in self.window_advance_days_map:
            return self.window_advance_days_map[wid]

        adv = getattr(obs, "advance_days", None)
        if adv is not None and isinstance(adv, int) and adv in {1, 7, 15, 30, 45}:
            return adv

        return None

    def filter_eligible_observations(
        self, observations: List[FareObservation]
    ) -> List[FareObservation]:
        """Filter raw observations for statistical aggregation eligibility.

        Eligibility criteria:
        - Must be available (is_available is True)
        - Must have positive fare (total_fare > 0)
        - Must not be flagged as MISSING, INVALID_FARE, SOLD_OUT, or DUPLICATE
        - Excludes OUTLIER if exclude_outliers is True
        - Travel-Date Consistency: travel_date == observation_date + target_advance_days
        """
        eligible = []
        for obs in observations:
            status = getattr(obs, "quality_status", "VALID")
            is_avail = getattr(obs, "is_available", True)
            fare = getattr(obs, "total_fare", None)

            if not is_avail:
                continue
            if fare is None or Decimal(str(fare)) <= Decimal("0"):
                continue

            if status in {"MISSING", "INVALID_FARE", "SOLD_OUT", "DUPLICATE", "CANCELLED", "SCRAPE_ERROR"}:
                continue

            if self.exclude_outliers and status == "OUTLIER":
                continue

            # Travel-date consistency validation
            # Required invariant: travel_date == observation_date + target_advance_days
            obs_dt = getattr(obs, "observed_at", None)
            if hasattr(obs_dt, "date"):
                obs_date = obs_dt.date()
            elif isinstance(obs_dt, date):
                obs_date = obs_dt
            else:
                obs_date = None

            travel_date = getattr(obs, "travel_date", None)
            target_adv = self.resolve_target_advance_days(obs)

            if obs_date is not None and travel_date is not None and target_adv is not None:
                expected_travel_date = obs_date + timedelta(days=target_adv)
                if travel_date != expected_travel_date:
                    logger.warning(
                        "Inconsistent travel date for observation %s (flight %s): "
                        "travel_date=%s != expected %s (obs_date=%s + %d days). "
                        "Excluding observation from aggregation.",
                        getattr(obs, "observation_id", getattr(obs, "id", "?")),
                        getattr(obs, "flight_number", "?"),
                        travel_date,
                        expected_travel_date,
                        obs_date,
                        target_adv,
                    )
                    continue

            eligible.append(obs)

        logger.debug(
            "Filtered %d observations to %d eligible candidates (exclude_outliers=%s)",
            len(observations),
            len(eligible),
            self.exclude_outliers,
        )
        return eligible

    def aggregate_cohorts(
        self, observations: List[FareObservation]
    ) -> List[RouteDailySummary]:
        """Group eligible observations by (observation_date, route_id, window_id) and build summaries.

        Date Semantics & Cohort Safety:
        - observation_date: Date quotes were observed (obs.observed_at.date())
        - target_travel_date: Date of scheduled flight departure (obs.travel_date)
        - window_id: Target advance purchase window

        Safety Invariants:
        1. All observations in a cohort must have the exact same target_travel_date.
        2. Combining different target travel dates into one RouteDailySummary is strictly prohibited.
        3. If inconsistent dates are detected within a cohort, the cohort is rejected.
        """
        eligible = self.filter_eligible_observations(observations)
        if not eligible:
            logger.info("No eligible observations found for aggregation.")
            return []

        # Cohort Key = (observation_date, route_id, window_id)
        cohort_map: Dict[Tuple[date, int, int], List[FareObservation]] = defaultdict(list)
        for obs in eligible:
            obs_dt = getattr(obs, "observed_at", None)
            if hasattr(obs_dt, "date"):
                obs_date = obs_dt.date()
            elif isinstance(obs_dt, date):
                obs_date = obs_dt
            else:
                obs_date = date.today()

            route_id = getattr(obs, "route_id", 0)
            window_id = getattr(obs, "window_id", None) or getattr(obs, "booking_window_id", 0)

            cohort_map[(obs_date, route_id, window_id)].append(obs)

        summaries: List[RouteDailySummary] = []

        for (obs_date, route_id, window_id), cohort_obs in cohort_map.items():
            # Cohort Safety: Check target travel date consistency within the cohort
            distinct_travel_dates = {
                getattr(o, "travel_date", None)
                for o in cohort_obs
                if getattr(o, "travel_date", None) is not None
            }

            if not distinct_travel_dates:
                logger.warning(
                    "Cohort (obs_date=%s, route=%d, window=%d) has no travel dates. Skipping.",
                    obs_date,
                    route_id,
                    window_id,
                )
                continue

            if len(distinct_travel_dates) > 1:
                logger.error(
                    "Cohort safety violation: Conflicting target travel dates detected for "
                    "cohort (obs_date=%s, route_id=%d, window_id=%d): %s. "
                    "Rejecting cohort from aggregation to prevent mixing different travel dates.",
                    obs_date,
                    route_id,
                    window_id,
                    sorted(distinct_travel_dates),
                )
                continue

            target_travel_date = next(iter(distinct_travel_dates))

            # Additional cohort safety check: verify against window target advance days
            target_adv = self.window_advance_days_map.get(window_id)
            if target_adv is not None:
                expected_travel_date = obs_date + timedelta(days=target_adv)
                if target_travel_date != expected_travel_date:
                    logger.error(
                        "Cohort safety violation: Cohort target_travel_date (%s) does not match "
                        "expected (%s) for window_id=%d (target_advance_days=%d). Rejecting cohort.",
                        target_travel_date,
                        expected_travel_date,
                        window_id,
                        target_adv,
                    )
                    continue

            # Step 1: Reconcile multiple source quotes for the same physical scheduled flight
            reconciled_flights = reconcile_cross_source_quotes(
                cohort_obs, policy=self.cross_source_policy
            )
            flight_fares = [rf.reconciled_fare for rf in reconciled_flights]

            # Step 2: Calculate robust statistical metrics
            stats = calculate_route_summary_stats(
                fares=flight_fares, metric=self.representative_metric
            )

            summary = RouteDailySummary(
                observation_date=obs_date,
                route_id=route_id,
                window_id=window_id,
                target_travel_date=target_travel_date,
                observations_count=stats.count,
                min_fare=stats.min_fare,
                median_fare=stats.median_fare,
                mean_fare=stats.mean_fare,
                geometric_mean_fare=stats.geometric_mean_fare,
                representative_fare=stats.representative_fare,
                is_imputed=False,
                imputation_method=None,
            )
            summaries.append(summary)

            logger.debug(
                "Aggregated cohort (obs_date=%s, route=%d, window=%d): count=%d, rep_fare=%.2f (GM=%.2f)",
                obs_date,
                route_id,
                window_id,
                stats.count,
                stats.representative_fare,
                stats.geometric_mean_fare,
            )

        return summaries

    def persist_summaries(
        self, summaries: List[RouteDailySummary]
    ) -> AggregationResult:
        """Idempotently persist or update RouteDailySummary records in PostgreSQL.

        Uses uniqueness on (observation_date, route_id, window_id) to update existing
        records without duplicate key errors.
        """
        if self.db is None:
            raise ValueError("Database session required for persist_summaries().")

        created = 0
        updated = 0
        persisted_records: List[RouteDailySummary] = []

        try:
            for summ in summaries:
                # Query existing record
                stmt = select(RouteDailySummary).where(
                    RouteDailySummary.observation_date == summ.observation_date,
                    RouteDailySummary.route_id == summ.route_id,
                    RouteDailySummary.window_id == summ.window_id,
                )
                existing = self.db.scalars(stmt).first()

                if existing is not None:
                    # Update existing record in place
                    existing.target_travel_date = summ.target_travel_date
                    existing.observations_count = summ.observations_count
                    existing.min_fare = summ.min_fare
                    existing.median_fare = summ.median_fare
                    existing.mean_fare = summ.mean_fare
                    existing.geometric_mean_fare = summ.geometric_mean_fare
                    existing.representative_fare = summ.representative_fare
                    existing.is_imputed = summ.is_imputed
                    existing.imputation_method = summ.imputation_method
                    persisted_records.append(existing)
                    updated += 1
                else:
                    self.db.add(summ)
                    persisted_records.append(summ)
                    created += 1

            self.db.commit()
            logger.info("Persisted RouteDailySummary: %d created, %d updated", created, updated)

            return AggregationResult(
                total_observations_evaluated=0,
                eligible_observations_count=0,
                cohorts_formed=len(summaries),
                summaries_created=created,
                summaries_updated=updated,
                summaries=persisted_records,
            )

        except Exception as exc:
            self.db.rollback()
            logger.error("Database error while persisting RouteDailySummary: %s", exc)
            raise

    def process_and_persist(
        self, observations: List[FareObservation]
    ) -> AggregationResult:
        """Complete workflow: filter, aggregate cohorts, and persist idempotently."""
        eligible = self.filter_eligible_observations(observations)
        summaries = self.aggregate_cohorts(observations)

        if self.db is not None and summaries:
            res = self.persist_summaries(summaries)
            res.total_observations_evaluated = len(observations)
            res.eligible_observations_count = len(eligible)
            return res

        return AggregationResult(
            total_observations_evaluated=len(observations),
            eligible_observations_count=len(eligible),
            cohorts_formed=len(summaries),
            summaries_created=len(summaries),
            summaries_updated=0,
            summaries=summaries,
        )
