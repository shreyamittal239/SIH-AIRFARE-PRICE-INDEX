"""Cross-source flight quotation reconciliation.

When multiple channels (airline direct portals, OTAs) quote the same physical
scheduled flight, reconciles them into a single comparable flight-level fare.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from enum import Enum
import logging
from typing import Any, Dict, List, Optional

import numpy as np

from backend.app.db.models.fare_observation import FareObservation

logger = logging.getLogger(__name__)


class CrossSourcePolicy(str, Enum):
    """Supported cross-source fare reconciliation policies."""

    MIN_VALID_FARE = "MIN_VALID_FARE"
    AIRLINE_DIRECT_PREFERRED = "AIRLINE_DIRECT_PREFERRED"
    MEAN_VALID_FARE = "MEAN_VALID_FARE"
    MEDIAN_VALID_FARE = "MEDIAN_VALID_FARE"


@dataclass
class ReconciledFlight:
    """A reconciled, flight-level comparable quotation."""

    flight_identity_key: str
    airline_id: int
    flight_number: str
    route_id: int
    travel_date: date
    scheduled_departure_time: Optional[time]
    cabin_class: str
    reconciled_fare: Decimal
    selected_observation_id: Optional[int] = None
    selected_source_id: Optional[int] = None
    all_quotes_count: int = 1
    sources_represented: List[int] = field(default_factory=list)
    source_observations: List[FareObservation] = field(default_factory=list)


def get_flight_identity_key(obs: Any) -> str:
    """Compute physical scheduled flight identity key.

    Rule: airline_id + flight_number + route_id + travel_date + scheduled_departure_time + cabin_class.
    """
    airline_id = getattr(obs, "airline_id", "")
    flight_num = str(getattr(obs, "flight_number", "")).strip().upper()
    route_id = getattr(obs, "route_id", "")
    travel_date = str(getattr(obs, "travel_date", ""))
    dep_time = str(getattr(obs, "scheduled_departure_time", "NONE"))
    cabin = str(getattr(obs, "cabin_class", "ECONOMY")).strip().upper()

    return f"{airline_id}|{flight_num}|{route_id}|{travel_date}|{dep_time}|{cabin}"


def reconcile_cross_source_quotes(
    observations: List[FareObservation],
    policy: CrossSourcePolicy = CrossSourcePolicy.MIN_VALID_FARE,
) -> List[ReconciledFlight]:
    """Reconcile multiple source quotes for identical flights into comparable flight fares.

    Args:
        observations: Eligible FareObservation instances.
        policy: CrossSourcePolicy strategy. Defaults to MIN_VALID_FARE.

    Returns:
        List of ReconciledFlight objects, one per unique physical flight product.
    """
    # Group observations by flight identity
    grouped_flights: Dict[str, List[FareObservation]] = defaultdict(list)
    for obs in observations:
        key = get_flight_identity_key(obs)
        grouped_flights[key].append(obs)

    reconciled_list: List[ReconciledFlight] = []

    for flight_key, quotes in grouped_flights.items():
        first = quotes[0]
        airline_id = getattr(first, "airline_id", 0)
        flight_number = getattr(first, "flight_number", "")
        route_id = getattr(first, "route_id", 0)
        travel_date = getattr(first, "travel_date", date.today())
        dep_time = getattr(first, "scheduled_departure_time", None)
        cabin = getattr(first, "cabin_class", "ECONOMY")

        sources = [getattr(q, "data_source_id", 0) for q in quotes]
        fares = [Decimal(str(getattr(q, "total_fare", "0"))) for q in quotes]

        selected_obs = quotes[0]
        final_fare = fares[0]

        if len(quotes) == 1:
            final_fare = fares[0]
            selected_obs = quotes[0]
        elif policy == CrossSourcePolicy.MIN_VALID_FARE:
            # Prototype Decision: Pick lowest valid consumer fare across sources
            selected_obs = min(quotes, key=lambda q: Decimal(str(getattr(q, "total_fare"))))
            final_fare = Decimal(str(getattr(selected_obs, "total_fare")))
        elif policy == CrossSourcePolicy.MEAN_VALID_FARE:
            # Arithmetic mean across available source quotations
            mean_val = sum(fares) / len(fares)
            final_fare = round(mean_val, 2)
            selected_obs = quotes[0]
        elif policy == CrossSourcePolicy.MEDIAN_VALID_FARE:
            float_fares = [float(f) for f in fares]
            med_val = float(np.median(float_fares))
            final_fare = round(Decimal(str(med_val)), 2)
            selected_obs = min(quotes, key=lambda q: abs(Decimal(str(getattr(q, "total_fare"))) - final_fare))
        elif policy == CrossSourcePolicy.AIRLINE_DIRECT_PREFERRED:
            # Prefer direct carrier quote if available
            direct_quotes = [q for q in quotes if "DIRECT" in str(getattr(getattr(q, "data_source", None), "source_type", "")).upper()]
            if direct_quotes:
                selected_obs = min(direct_quotes, key=lambda q: Decimal(str(getattr(q, "total_fare"))))
            else:
                selected_obs = min(quotes, key=lambda q: Decimal(str(getattr(q, "total_fare"))))
            final_fare = Decimal(str(getattr(selected_obs, "total_fare")))

        logger.debug(
            "Reconciled %s (%d quotes across %s): final_fare=%.2f using %s",
            flight_key,
            len(quotes),
            sources,
            final_fare,
            policy.value,
        )

        reconciled_list.append(
            ReconciledFlight(
                flight_identity_key=flight_key,
                airline_id=airline_id,
                flight_number=flight_number,
                route_id=route_id,
                travel_date=travel_date,
                scheduled_departure_time=dep_time,
                cabin_class=cabin,
                reconciled_fare=final_fare,
                selected_observation_id=getattr(selected_obs, "observation_id", None),
                selected_source_id=getattr(selected_obs, "data_source_id", None),
                all_quotes_count=len(quotes),
                sources_represented=list(set(sources)),
                source_observations=quotes,
            )
        )

    return reconciled_list
