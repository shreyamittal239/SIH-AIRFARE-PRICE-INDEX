"""Outlier detection for airfare observations within homogeneous cohorts.

Evaluates quotations within route + travel_date + booking_window + cabin_class cohorts
using configurable IQR or MAD statistical bounds with minimum sample guardrails.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backend.app.db.models.fare_observation import FareObservation
from backend.processing.cleaning.validators import QualityStatus

logger = logging.getLogger(__name__)


class OutlierMethod(str, Enum):
    """Supported statistical outlier detection methods."""

    IQR = "IQR"
    MAD = "MAD"


# Prototype cleaning parameters for degenerate / zero-dispersion cohorts.
# When IQR or MAD collapses to zero (e.g. >75% identical prices), a zero-width
# acceptance interval [median, median] would erroneously classify normal minor
# price differences as outliers. A configurable tolerance band (combining relative
# and absolute allowances around the median) is applied instead.
# Note: These are heuristic safeguard parameters for the prototype cleaning pipeline
# and do not represent official MoSPI statistical methodology.
DEFAULT_LOW_DISPERSION_RELATIVE_TOLERANCE: float = 0.10  # 10% allowable deviation from median
DEFAULT_LOW_DISPERSION_ABSOLUTE_TOLERANCE: float = 500.0  # ₹500 minimum absolute tolerance band


@dataclass
class CohortStats:
    """Statistical summary for an observation cohort."""

    cohort_key: Tuple
    count: int
    min_fare: float
    max_fare: float
    q1: Optional[float] = None
    median: Optional[float] = None
    q3: Optional[float] = None
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    outlier_count: int = 0
    is_low_dispersion: bool = False


@dataclass
class OutlierDetectionResult:
    """Results of outlier detection across cohorts."""

    clean_observations: List[FareObservation] = field(default_factory=list)
    outliers: List[FareObservation] = field(default_factory=list)
    cohort_stats: Dict[Tuple, CohortStats] = field(default_factory=dict)


def get_cohort_key(obs: Any) -> Tuple:
    """Construct the homogeneous comparison group key.

    Rule: Airfare must only be compared against flights on the same route,
    traveling on the same date, within the same booking window and cabin class.
    """
    route_id = getattr(obs, "route_id", 0)
    travel_date = str(getattr(obs, "travel_date", ""))
    window_id = getattr(obs, "window_id", 0)
    cabin_class = str(getattr(obs, "cabin_class", "ECONOMY")).strip().upper()

    return (route_id, travel_date, window_id, cabin_class)


class OutlierDetector:
    """Configurable outlier detection engine for airfare observations."""

    def __init__(
        self,
        method: OutlierMethod = OutlierMethod.IQR,
        iqr_multiplier: float = 1.5,
        mad_threshold: float = 3.0,
        min_sample_size: int = 5,
        low_dispersion_relative_tolerance: float = DEFAULT_LOW_DISPERSION_RELATIVE_TOLERANCE,
        low_dispersion_absolute_tolerance: float = DEFAULT_LOW_DISPERSION_ABSOLUTE_TOLERANCE,
    ) -> None:
        """Initialize detector settings.

        Args:
            method: Statistical method (IQR or MAD).
            iqr_multiplier: Multiplier for IQR bounds (default 1.5).
            mad_threshold: Modified Z-score threshold for MAD (default 3.0).
            min_sample_size: Minimum observations required in a cohort before
                             applying statistical outlier detection. Cohorts with
                             fewer observations will NOT flag outliers.
            low_dispersion_relative_tolerance: Fractional tolerance around median
                when cohort dispersion collapses to zero (default 0.10, i.e. 10%).
            low_dispersion_absolute_tolerance: Absolute fare tolerance in currency
                units (e.g. INR) around median when dispersion collapses (default 500.0).
        """
        self.method = method
        self.iqr_multiplier = iqr_multiplier
        self.mad_threshold = mad_threshold
        self.min_sample_size = min_sample_size
        self.low_dispersion_relative_tolerance = low_dispersion_relative_tolerance
        self.low_dispersion_absolute_tolerance = low_dispersion_absolute_tolerance

    def detect_outliers(
        self, observations: List[FareObservation]
    ) -> OutlierDetectionResult:
        """Detect and flag statistical outliers within homogeneous cohorts.

        Args:
            observations: Input observations passing basic field validation.

        Returns:
            OutlierDetectionResult with clean observations, flagged outliers,
            and per-cohort statistical parameters.
        """
        result = OutlierDetectionResult()

        # Group observations into cohorts
        cohort_map = defaultdict(list)
        for obs in observations:
            key = get_cohort_key(obs)
            cohort_map[key].append(obs)

        for cohort_key, cohort_obs in cohort_map.items():
            fares = [float(getattr(o, "total_fare", 0.0)) for o in cohort_obs]
            n = len(fares)

            # Rule: If sample size is too small, statistical detection is unreliable;
            # retain all observations as valid rather than guessing.
            if n < self.min_sample_size:
                logger.debug(
                    "Cohort %s has N=%d (< min_sample_size=%d). Outlier detection skipped.",
                    cohort_key,
                    n,
                    self.min_sample_size,
                )
                result.clean_observations.extend(cohort_obs)
                result.cohort_stats[cohort_key] = CohortStats(
                    cohort_key=cohort_key,
                    count=n,
                    min_fare=min(fares) if fares else 0.0,
                    max_fare=max(fares) if fares else 0.0,
                    median=float(np.median(fares)) if fares else 0.0,
                )
                continue

            # Compute outlier bounds based on configured method
            if self.method == OutlierMethod.MAD:
                lower, upper, stats = self._compute_mad_bounds(cohort_key, fares)
            else:
                lower, upper, stats = self._compute_iqr_bounds(cohort_key, fares)

            result.cohort_stats[cohort_key] = stats

            # Classify observations against bounds
            for obs in cohort_obs:
                f_val = float(getattr(obs, "total_fare", 0.0))
                if f_val < lower or f_val > upper:
                    logger.info(
                        "Outlier flagged in cohort %s: flight=%s, fare=%.2f "
                        "(bounds: [%.2f, %.2f], low_dispersion=%s)",
                        cohort_key,
                        getattr(obs, "flight_number", "UNKNOWN"),
                        f_val,
                        lower,
                        upper,
                        stats.is_low_dispersion,
                    )
                    setattr(obs, "quality_status", QualityStatus.OUTLIER.value)
                    result.outliers.append(obs)
                    stats.outlier_count += 1
                else:
                    setattr(obs, "quality_status", QualityStatus.VALID.value)
                    result.clean_observations.append(obs)

        return result

    def _compute_iqr_bounds(
        self, cohort_key: Tuple, fares: List[float]
    ) -> Tuple[float, float, CohortStats]:
        """Compute lower and upper bounds using Interquartile Range (IQR).

        If IQR collapses to zero (degenerate / low dispersion), evaluates a configurable
        tolerance band around the median rather than treating [median, median] as an exact boundary.
        """
        arr = np.array(fares)
        q1 = float(np.percentile(arr, 25))
        q3 = float(np.percentile(arr, 75))
        median = float(np.median(arr))
        iqr = q3 - q1

        # Check for degenerate/zero dispersion (e.g. >75% identical observations)
        if iqr <= 0.0:
            tolerance = max(
                self.low_dispersion_absolute_tolerance,
                median * self.low_dispersion_relative_tolerance,
            )
            lower = max(0.0, median - tolerance)
            upper = median + tolerance
            is_low_disp = True
            logger.info(
                "Cohort %s has zero dispersion (IQR=0.0, median=%.2f). "
                "Applied low-dispersion tolerance: tolerance=%.2f, bounds=[%.2f, %.2f]",
                cohort_key,
                median,
                tolerance,
                lower,
                upper,
            )
        else:
            lower = max(0.0, q1 - (self.iqr_multiplier * iqr))
            upper = q3 + (self.iqr_multiplier * iqr)
            is_low_disp = False

        stats = CohortStats(
            cohort_key=cohort_key,
            count=len(fares),
            min_fare=float(arr.min()),
            max_fare=float(arr.max()),
            q1=q1,
            median=median,
            q3=q3,
            lower_bound=lower,
            upper_bound=upper,
            is_low_dispersion=is_low_disp,
        )
        return lower, upper, stats

    def _compute_mad_bounds(
        self, cohort_key: Tuple, fares: List[float]
    ) -> Tuple[float, float, CohortStats]:
        """Compute lower and upper bounds using Median Absolute Deviation (MAD)."""
        arr = np.array(fares)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))

        # If MAD is zero (more than half identical fares), fallback to IQR
        if mad == 0.0:
            return self._compute_iqr_bounds(cohort_key, fares)

        # 0.6745 is the consistency factor for normal distributions
        cutoff = (self.mad_threshold * mad) / 0.6745
        lower = max(0.0, median - cutoff)
        upper = median + cutoff

        stats = CohortStats(
            cohort_key=cohort_key,
            count=len(fares),
            min_fare=float(arr.min()),
            max_fare=float(arr.max()),
            median=median,
            lower_bound=lower,
            upper_bound=upper,
        )
        return lower, upper, stats
