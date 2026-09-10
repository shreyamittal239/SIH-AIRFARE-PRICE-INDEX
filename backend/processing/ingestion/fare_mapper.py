"""Mapper from FlightQuote to SQLAlchemy FareObservation.

Translates normalized scraper quotes into persistent relational entities
using resolved dimensional foreign keys and deterministic product fingerprints.
"""

from typing import Optional

from backend.app.db.models.fare_observation import FareObservation
from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.processing.ingestion.fingerprint import compute_flight_fingerprint
from backend.processing.ingestion.resolver import ResolvedDimensions


def map_quote_to_observation(
    quote: FlightQuote,
    run_id: int,
    dimensions: ResolvedDimensions,
    quality_status: str = "VALID",
) -> FareObservation:
    """Map a validated FlightQuote and its resolved dimensions to a FareObservation.

    Args:
        quote: Source FlightQuote schema object.
        run_id: Active CollectionRun primary key.
        dimensions: Resolved relational foreign keys.
        quality_status: Initial observation status ('VALID' by default).

    Returns:
        SQLAlchemy FareObservation model ready for session attachment.
    """
    fingerprint = compute_flight_fingerprint(quote)
    is_non_stop = bool(quote.stops == 0 or quote.stops is None)

    return FareObservation(
        run_id=run_id,
        data_source_id=dimensions.data_source_id,
        route_id=dimensions.route_id,
        airline_id=dimensions.airline_id,
        window_id=dimensions.window_id,
        flight_number=quote.flight_number,
        observed_at=quote.observed_at,
        travel_date=quote.travel_date,
        advance_days=dimensions.advance_days,
        scheduled_departure_time=quote.departure_time,
        scheduled_arrival_time=quote.arrival_time,
        origin_airport_code=quote.origin,
        destination_airport_code=quote.destination,
        cabin_class=quote.cabin_class,
        fare_family=quote.fare_family,
        is_non_stop=is_non_stop,
        stops=quote.stops,
        total_fare=quote.total_fare,
        base_fare=None,
        fuel_surcharge=None,
        taxes_and_fees=None,
        convenience_fee=None,
        currency=quote.currency,
        is_available=quote.availability,
        seats_remaining=None,
        quality_status=quality_status,
        fingerprint_hash=fingerprint,
    )
