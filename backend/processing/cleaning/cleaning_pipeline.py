"""End-to-end data cleaning and quality validation pipeline.

Orchestrates deterministic validation, availability checks, exact and cross-source
deduplication, and cohort-specific outlier detection.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.app.db.models.fare_observation import FareObservation
from backend.processing.cleaning.deduplication import deduplicate_observations
from backend.processing.cleaning.outlier_detection import OutlierDetector
from backend.processing.cleaning.validators import QualityStatus, validate_observation

logger = logging.getLogger(__name__)


@dataclass
class CleaningSummary:
    """Structured metrics and output collections of cleaning execution."""

    total: int = 0
    valid: int = 0
    missing: int = 0
    invalid_fare: int = 0
    sold_out: int = 0
    cancelled: int = 0
    duplicate: int = 0
    outlier: int = 0
    clean_observations: List[FareObservation] = field(default_factory=list)
    flagged_observations: List[Tuple[FareObservation, str, str]] = field(
        default_factory=list
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to clean dictionary representation."""
        return {
            "total": self.total,
            "valid": self.valid,
            "missing": self.missing,
            "invalid_fare": self.invalid_fare,
            "sold_out": self.sold_out,
            "cancelled": self.cancelled,
            "duplicate": self.duplicate,
            "outlier": self.outlier,
            "clean_observations": self.clean_observations,
        }


class CleaningPipeline:
    """Non-destructive data quality and cleaning pipeline for airfare observations."""

    def __init__(self, outlier_detector: Optional[OutlierDetector] = None) -> None:
        """Initialize pipeline with outlier detector settings."""
        self.outlier_detector = outlier_detector or OutlierDetector()

    def clean(self, observations: List[FareObservation]) -> CleaningSummary:
        """Execute quality validation, deduplication, and outlier detection.

        Args:
            observations: Raw observations to clean.

        Returns:
            CleaningSummary with counts and the clean observations subset.
        """
        summary = CleaningSummary(total=len(observations))
        candidates_for_dedup: List[FareObservation] = []

        # Stage 1: Basic validation & availability check
        for obs in observations:
            val_res = validate_observation(obs)
            if not val_res.is_valid:
                summary.flagged_observations.append((obs, val_res.status, val_res.reason or ""))
                setattr(obs, "quality_status", val_res.status)

                if val_res.status == QualityStatus.MISSING.value:
                    summary.missing += 1
                elif val_res.status == QualityStatus.INVALID_FARE.value:
                    summary.invalid_fare += 1
                elif val_res.status == QualityStatus.SOLD_OUT.value:
                    summary.sold_out += 1
                elif val_res.status == QualityStatus.CANCELLED.value:
                    summary.cancelled += 1
            else:
                candidates_for_dedup.append(obs)

        # Stage 2: Deduplication (identifying exact duplicates)
        dedup_res = deduplicate_observations(candidates_for_dedup)
        for dup in dedup_res.exact_duplicates:
            summary.flagged_observations.append((dup, QualityStatus.DUPLICATE.value, "Exact duplicate quotation"))
            summary.duplicate += 1

        candidates_for_outliers = dedup_res.clean_observations

        # Stage 3: Outlier detection within homogeneous cohorts
        outlier_res = self.outlier_detector.detect_outliers(candidates_for_outliers)
        for out in outlier_res.outliers:
            summary.flagged_observations.append(
                (out, QualityStatus.OUTLIER.value, "Statistical price outlier within cohort")
            )
            summary.outlier += 1

        # Stage 4: Compile clean observation set
        summary.clean_observations = outlier_res.clean_observations
        summary.valid = len(summary.clean_observations)

        logger.info(
            "Cleaning pipeline completed: total=%d, valid=%d, missing=%d, invalid_fare=%d, "
            "sold_out=%d, duplicate=%d, outlier=%d",
            summary.total,
            summary.valid,
            summary.missing,
            summary.invalid_fare,
            summary.sold_out,
            summary.duplicate,
            summary.outlier,
        )
        return summary

    @staticmethod
    def group_for_representative_fare(
        clean_observations: List[FareObservation],
    ) -> Dict[Tuple[int, date, int], List[FareObservation]]:
        """Group clean observations by route, target travel date, and booking window.

        Prepares clean observations for downstream daily representative fare calculation:
        Cohort Key = (route_id, travel_date, window_id)
        """
        grouped = defaultdict(list)
        for obs in clean_observations:
            route_id = getattr(obs, "route_id", 0)
            t_date = getattr(obs, "travel_date", None)
            window_id = getattr(obs, "window_id", 0)
            if route_id and t_date and window_id:
                grouped[(route_id, t_date, window_id)].append(obs)
        return dict(grouped)
