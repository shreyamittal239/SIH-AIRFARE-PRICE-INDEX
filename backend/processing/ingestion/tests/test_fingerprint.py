"""Unit tests for deterministic flight product fingerprinting."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
import pytest

from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.processing.ingestion.fingerprint import (
    compute_flight_fingerprint,
    get_canonical_fingerprint_string,
    normalize_flight_number,
)


def create_sample_quote(
    airline: str = "SpiceJet",
    flight_number: str = "SG 162",
    origin: str = "DEL",
    destination: str = "BOM",
    travel_date: date = date(2026, 9, 15),
    departure_time: time = time(19, 55),
    cabin_class: str = "ECONOMY",
    fare_family: str = "SpiceSaver",
    total_fare: Decimal = Decimal("7890.00"),
    observed_at: datetime = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc),
) -> FlightQuote:
    """Helper to build a sample FlightQuote."""
    return FlightQuote(
        airline=airline,
        flight_number=flight_number,
        origin=origin,
        destination=destination,
        travel_date=travel_date,
        departure_time=departure_time,
        arrival_time=time(22, 40),
        cabin_class=cabin_class,
        fare_family=fare_family,
        stops=0,
        total_fare=total_fare,
        currency="INR",
        availability=True,
        source="SpiceJet Direct",
        observed_at=observed_at,
    )


def test_fingerprint_canonical_string_format():
    """Verify exact pipe-delimited normalized canonical structure."""
    canonical = get_canonical_fingerprint_string(
        airline="  SpiceJet  ",
        flight_number="sg-162",
        origin="del",
        destination="bom",
        travel_date=date(2026, 9, 15),
        scheduled_departure_time=time(19, 55),
        cabin_class="Economy",
        fare_family="SpiceSaver",
    )
    expected = "SPICEJET|SG 162|DEL|BOM|2026-09-15|19:55|ECONOMY|SPICESAVER"
    assert canonical == expected


def test_fingerprint_canonical_string_none_handling():
    """Verify that None values are rendered as 'NONE'."""
    canonical = get_canonical_fingerprint_string(
        airline="SpiceJet",
        flight_number="SG 2802",
        origin="DEL",
        destination="BOM",
        travel_date="2026-09-15",
        scheduled_departure_time=None,
        cabin_class=None,
        fare_family=None,
    )
    expected = "SPICEJET|SG 2802|DEL|BOM|2026-09-15|NONE|ECONOMY|NONE"
    assert canonical == expected


def test_fingerprint_deterministic_stability():
    """Verify that the same FlightQuote always yields the identical 64-char hex hash."""
    q1 = create_sample_quote()
    q2 = create_sample_quote()

    fp1 = compute_flight_fingerprint(q1)
    fp2 = compute_flight_fingerprint(q2)

    assert fp1 == fp2
    assert len(fp1) == 64
    assert all(c in "0123456789abcdef" for c in fp1)


def test_fingerprint_ignores_observed_at():
    """Verify that observations at different timestamps produce the exact same fingerprint."""
    q1 = create_sample_quote(observed_at=datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc))
    q2 = create_sample_quote(observed_at=datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc))

    assert compute_flight_fingerprint(q1) == compute_flight_fingerprint(q2)


def test_fingerprint_ignores_total_fare():
    """Verify that price volatility does not change the physical flight-product fingerprint."""
    q1 = create_sample_quote(total_fare=Decimal("7890.00"))
    q2 = create_sample_quote(total_fare=Decimal("15200.00"))

    assert compute_flight_fingerprint(q1) == compute_flight_fingerprint(q2)


def test_fingerprint_changes_on_identity_fields():
    """Verify that altering identity components yields distinct fingerprints."""
    base = create_sample_quote()
    base_fp = compute_flight_fingerprint(base)

    # Different flight number
    diff_flight = create_sample_quote(flight_number="SG 2802")
    assert compute_flight_fingerprint(diff_flight) != base_fp

    # Different travel date
    diff_date = create_sample_quote(travel_date=date(2026, 9, 16))
    assert compute_flight_fingerprint(diff_date) != base_fp

    # Different departure time
    diff_time = create_sample_quote(departure_time=time(20, 0))
    assert compute_flight_fingerprint(diff_time) != base_fp

    # Different destination
    diff_dest = create_sample_quote(destination="BLR")
    assert compute_flight_fingerprint(diff_dest) != base_fp

    # Different fare family
    diff_fare_family = create_sample_quote(fare_family="SpiceMax")
    assert compute_flight_fingerprint(diff_fare_family) != base_fp


def test_normalize_flight_number():
    """Test standardizing flight number formatting."""
    assert normalize_flight_number("SG162") == "SG 162"
    assert normalize_flight_number("sg-162") == "SG 162"
    assert normalize_flight_number("SG  162") == "SG 162"
    assert normalize_flight_number("6E-204") == "6E 204"
    assert normalize_flight_number("AI101") == "AI 101"
