"""Deduplication logic distinguishing exact duplicates from cross-source quotations.

Separates intra-run/source duplicates (flagged as DUPLICATE) from cross-source
observations (grouped and preserved for downstream statistical weighting).
"""

from collections import defaultdict
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List

from backend.app.db.models.fare_observation import FareObservation
from backend.processing.cleaning.validators import QualityStatus

logger = logging.getLogger(__name__)


@dataclass
class DeduplicationResult:
    """Outcome of observation deduplication."""

    clean_observations: List[FareObservation] = field(default_factory=list)
    exact_duplicates: List[FareObservation] = field(default_factory=list)
    cross_source_groups: Dict[str, List[FareObservation]] = field(default_factory=dict)


def get_cross_source_identity_key(obs: Any) -> str:
    """Compute physical flight identity key independent of data source and price.

    Groups quotes for the same scheduled flight across direct and OTA channels.
    """
    airline_id = getattr(obs, "airline_id", "")
    flight_num = str(getattr(obs, "flight_number", "")).strip().upper()
    route_id = getattr(obs, "route_id", "")
    travel_date = str(getattr(obs, "travel_date", ""))
    dep_time = str(getattr(obs, "scheduled_departure_time", "NONE"))
    cabin = str(getattr(obs, "cabin_class", "ECONOMY")).strip().upper()

    return f"{airline_id}|{flight_num}|{route_id}|{travel_date}|{dep_time}|{cabin}"


def deduplicate_observations(
    observations: List[FareObservation],
) -> DeduplicationResult:
    """Deduplicate observations into clean candidates, exact duplicates, and cross-source groups.

    Args:
        observations: Input list of FareObservation objects.

    Returns:
        DeduplicationResult containing non-duplicate observations, flagged exact duplicates,
        and cross-source groupings.
    """
    result = DeduplicationResult()

    # Track exact duplicates on (run_id, data_source_id, fingerprint_hash)
    seen_exact_fingerprints = set()
    first_pass_clean: List[FareObservation] = []

    for obs in observations:
        run_id = getattr(obs, "run_id", 0)
        source_id = getattr(obs, "data_source_id", 0)
        fp = getattr(obs, "fingerprint_hash", None)

        exact_key = (run_id, source_id, fp)

        if exact_key in seen_exact_fingerprints:
            logger.debug(
                "Exact duplicate identified: run=%s, source=%s, flight=%s, fp=%s",
                run_id,
                source_id,
                getattr(obs, "flight_number", "UNKNOWN"),
                str(fp)[:12] if fp else "",
            )
            # Update status to DUPLICATE on in-memory object
            setattr(obs, "quality_status", QualityStatus.DUPLICATE.value)
            result.exact_duplicates.append(obs)
        else:
            seen_exact_fingerprints.add(exact_key)
            first_pass_clean.append(obs)

    # Cross-source grouping: group remaining unique observations by physical flight identity
    groups_by_flight = defaultdict(list)
    for obs in first_pass_clean:
        cross_key = get_cross_source_identity_key(obs)
        groups_by_flight[cross_key].append(obs)

    for cross_key, group in groups_by_flight.items():
        # A group has cross-source multi-presence if observations come from >1 distinct data sources
        sources = {getattr(o, "data_source_id", 0) for o in group}
        if len(sources) > 1:
            result.cross_source_groups[cross_key] = group
            logger.debug(
                "Cross-source observation identified for flight key %s: %d sources (%s)",
                cross_key,
                len(sources),
                sources,
            )

    # Note: Cross-source observations are retained in clean_observations so downstream
    # aggregation can transparently decide how multiple sources contribute.
    result.clean_observations = first_pass_clean
    return result
