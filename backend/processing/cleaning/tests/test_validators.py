"""Unit tests for observation attribute and quality validators."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest

from backend.processing.cleaning.validators import (
    QualityStatus,
    validate_observation,
)


def make_mock_observation(
    run_id: int = 1,
    data_source_id: int = 1,
    route_id: int = 1,
    airline_id: int = 1,
    window_id: int = 2,
    flight_number: str = "SG 162",
    observed_at: datetime = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
    travel_date: date = date(2026, 9, 15),
    advance_days: int = 7,
    total_fare: Decimal = Decimal("7890.00"),
    currency: str = "INR",
    is_available: bool = True,
    cabin_class: str = "ECONOMY",
    quality_status: str = "VALID",
    fingerprint_hash: str = "41839f6c5c7e194e430f8983bc1ae2750e5028448ec6291a1d13f9c59f23cb53",
    **kwargs,
) -> SimpleNamespace:
    """Create a synthetic FareObservation for deterministic testing."""
    data = {
        "run_id": run_id,
        "data_source_id": data_source_id,
        "route_id": route_id,
        "airline_id": airline_id,
        "window_id": window_id,
        "flight_number": flight_number,
        "observed_at": observed_at,
        "travel_date": travel_date,
        "advance_days": advance_days,
        "total_fare": total_fare,
        "currency": currency,
        "is_available": is_available,
        "cabin_class": cabin_class,
        "quality_status": quality_status,
        "fingerprint_hash": fingerprint_hash,
        "scheduled_departure_time": time(19, 55),
        "scheduled_arrival_time": time(22, 40),
        "origin_airport_code": "DEL",
        "destination_airport_code": "BOM",
        "fare_family": "SpiceSaver",
        "is_non_stop": True,
        "stops": 0,
        "base_fare": None,
        "fuel_surcharge": None,
        "taxes_and_fees": None,
        "convenience_fee": None,
        "seats_remaining": None,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def test_validator_valid_observation():
    """Verify standard valid observation is accepted."""
    obs = make_mock_observation()
    result = validate_observation(obs)
    assert result.is_valid is True
    assert result.status == QualityStatus.VALID.value
    assert result.reason is None


def test_validator_zero_fare():
    """Verify total_fare == 0 is rejected as INVALID_FARE."""
    obs = make_mock_observation(total_fare=Decimal("0.00"))
    result = validate_observation(obs)
    assert result.is_valid is False
    assert result.status == QualityStatus.INVALID_FARE.value
    assert "Non-positive" in result.reason


def test_validator_negative_fare():
    """Verify negative total_fare is rejected as INVALID_FARE."""
    obs = make_mock_observation(total_fare=Decimal("-500.00"))
    result = validate_observation(obs)
    assert result.is_valid is False
    assert result.status == QualityStatus.INVALID_FARE.value


def test_validator_missing_required_foreign_key():
    """Verify missing route_id or airline_id is flagged as MISSING."""
    obs_no_route = make_mock_observation(route_id=None)
    res_route = validate_observation(obs_no_route)
    assert res_route.is_valid is False
    assert res_route.status == QualityStatus.MISSING.value

    obs_no_airline = make_mock_observation(airline_id=0)
    res_airline = validate_observation(obs_no_airline)
    assert res_airline.is_valid is False
    assert res_airline.status == QualityStatus.MISSING.value


def test_validator_missing_flight_number():
    """Verify missing flight number is flagged as MISSING."""
    obs = make_mock_observation(flight_number="")
    result = validate_observation(obs)
    assert result.is_valid is False
    assert result.status == QualityStatus.MISSING.value


def test_validator_missing_travel_date():
    """Verify missing travel_date is flagged as MISSING."""
    obs = make_mock_observation(travel_date=None)
    result = validate_observation(obs)
    assert result.is_valid is False
    assert result.status == QualityStatus.MISSING.value


def test_validator_sold_out_flight():
    """Verify is_available == False is flagged as SOLD_OUT."""
    obs = make_mock_observation(is_available=False)
    result = validate_observation(obs)
    assert result.is_valid is False
    assert result.status == QualityStatus.SOLD_OUT.value


def test_validator_nullable_scraper_fields_accepted():
    """Verify that scraper-tolerant nullable fields do not fail validation."""
    obs = make_mock_observation(
        base_fare=None,
        taxes_and_fees=None,
        fuel_surcharge=None,
        convenience_fee=None,
        seats_remaining=None,
        scheduled_departure_time=None,
        scheduled_arrival_time=None,
        fare_family=None,
    )
    result = validate_observation(obs)
    assert result.is_valid is True
    assert result.status == QualityStatus.VALID.value
