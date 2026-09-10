"""Deterministic fingerprint generation for flight fare products.

Generates a stable SHA-256 hexadecimal hash representing the intrinsic
identity of a quoted flight product across collection runs.
"""

from datetime import date, time
import hashlib
import re
from typing import Optional, Union

from backend.collectors.playwright.schemas.flight_quote import FlightQuote


def normalize_flight_number(val: str) -> str:
    """Normalize flight number (e.g. 'sg-162' -> 'SG 162', 'SG162' -> 'SG 162', '6E204' -> '6E 204')."""
    cleaned = re.sub(r"\s+", " ", val.strip().upper())
    match = re.match(r"^([A-Z]{2,3}|[0-9][A-Z]|[A-Z][0-9])\s*[-]?\s*(\d{1,4}[A-Z]?)$", cleaned)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return cleaned


def get_canonical_fingerprint_string(
    airline: str,
    flight_number: str,
    origin: str,
    destination: str,
    travel_date: Union[date, str],
    scheduled_departure_time: Optional[Union[time, str]] = None,
    cabin_class: Optional[str] = "ECONOMY",
    fare_family: Optional[str] = None,
) -> str:
    """Build the normalized canonical representation of a flight fare product.

    Fields included in canonical identity:
    1. airline (uppercase, trimmed)
    2. flight_number (normalized carrier code + number)
    3. origin (uppercase, 3-letter IATA)
    4. destination (uppercase, 3-letter IATA)
    5. travel_date (YYYY-MM-DD)
    6. scheduled_departure_time (HH:MM or 'NONE')
    7. cabin_class (uppercase, trimmed)
    8. fare_family (uppercase, trimmed, or 'NONE')

    Explicitly excluded:
    - observed_at (observation timestamp)
    - total_fare / price breakdown (mutable market quote, not product identity)
    - baggage / seats remaining (volatile availability attributes)

    Returns:
        Pipe-delimited canonical string.
    """
    c_airline = airline.strip().upper()
    c_flight_num = normalize_flight_number(flight_number)
    c_origin = origin.strip().upper()
    c_dest = destination.strip().upper()

    if isinstance(travel_date, date):
        c_travel_date = travel_date.strftime("%Y-%m-%d")
    else:
        c_travel_date = str(travel_date).strip()

    if isinstance(scheduled_departure_time, time):
        c_dep_time = scheduled_departure_time.strftime("%H:%M")
    elif scheduled_departure_time:
        c_dep_time = str(scheduled_departure_time).strip()
    else:
        c_dep_time = "NONE"

    c_cabin = (cabin_class or "ECONOMY").strip().upper()
    c_family = fare_family.strip().upper() if fare_family else "NONE"

    return f"{c_airline}|{c_flight_num}|{c_origin}|{c_dest}|{c_travel_date}|{c_dep_time}|{c_cabin}|{c_family}"


def compute_flight_fingerprint(quote: FlightQuote) -> str:
    """Compute the SHA-256 fingerprint hash from a FlightQuote object.

    Args:
        quote: Validated FlightQuote instance.

    Returns:
        64-character lowercase hexadecimal SHA-256 string.
    """
    canonical = get_canonical_fingerprint_string(
        airline=quote.airline,
        flight_number=quote.flight_number,
        origin=quote.origin,
        destination=quote.destination,
        travel_date=quote.travel_date,
        scheduled_departure_time=quote.departure_time,
        cabin_class=quote.cabin_class,
        fare_family=quote.fare_family,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
