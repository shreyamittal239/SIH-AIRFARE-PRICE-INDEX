"""Unit tests for exact and cross-source observation deduplication."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest

from backend.processing.cleaning.deduplication import (
    deduplicate_observations,
    get_cross_source_identity_key,
)
from backend.processing.cleaning.validators import QualityStatus


def make_obs(
    run_id: int = 1,
    data_source_id: int = 1,
    route_id: int = 10,
    airline_id: int = 2,
    flight_number: str = "SG 162",
    total_fare: Decimal = Decimal("7890.00"),
    fingerprint_hash: str = "fp_sg162_t7",
    departure_time: time = time(19, 55),
    travel_date: date = date(2026, 9, 15),
    cabin_class: str = "ECONOMY",
) -> SimpleNamespace:
    """Helper to create synthetic observations for deduplication testing."""
    return SimpleNamespace(
        run_id=run_id,
        data_source_id=data_source_id,
        route_id=route_id,
        airline_id=airline_id,
        flight_number=flight_number,
        total_fare=total_fare,
        fingerprint_hash=fingerprint_hash,
        scheduled_departure_time=departure_time,
        travel_date=travel_date,
        cabin_class=cabin_class,
        quality_status="VALID",
    )


def test_exact_duplicate_detected():
    """Verify that identical (run_id, data_source_id, fingerprint) is flagged as DUPLICATE."""
    obs1 = make_obs(run_id=1, data_source_id=1, fingerprint_hash="fp_sg162")
    obs2 = make_obs(run_id=1, data_source_id=1, fingerprint_hash="fp_sg162")  # Exact duplicate

    res = deduplicate_observations([obs1, obs2])

    assert len(res.clean_observations) == 1
    assert len(res.exact_duplicates) == 1
    assert res.clean_observations[0] is obs1
    assert res.exact_duplicates[0] is obs2
    assert obs2.quality_status == QualityStatus.DUPLICATE.value


def test_different_flight_not_duplicate():
    """Verify distinct flights in the same run are not flagged."""
    obs1 = make_obs(flight_number="SG 162", fingerprint_hash="fp_sg162")
    obs2 = make_obs(flight_number="SG 2802", fingerprint_hash="fp_sg2802")

    res = deduplicate_observations([obs1, obs2])

    assert len(res.clean_observations) == 2
    assert len(res.exact_duplicates) == 0


def test_same_flight_in_different_runs_not_duplicate():
    """Verify same flight across different runs is retained."""
    obs_run1 = make_obs(run_id=1, data_source_id=1, fingerprint_hash="fp_sg162")
    obs_run2 = make_obs(run_id=2, data_source_id=1, fingerprint_hash="fp_sg162")

    res = deduplicate_observations([obs_run1, obs_run2])

    assert len(res.clean_observations) == 2
    assert len(res.exact_duplicates) == 0


def test_cross_source_observations_grouped_and_preserved():
    """Verify that the same flight seen on multiple sources (direct vs OTA) is NOT discarded."""
    # Source 1: SpiceJet Direct (data_source_id=1)
    obs_direct = make_obs(
        run_id=1,
        data_source_id=1,
        total_fare=Decimal("7890.00"),
        fingerprint_hash="fp_sg162_direct",
    )
    # Source 2: MakeMyTrip (data_source_id=2)
    obs_ota = make_obs(
        run_id=1,
        data_source_id=2,
        total_fare=Decimal("7650.00"),
        fingerprint_hash="fp_sg162_ota",
    )

    res = deduplicate_observations([obs_direct, obs_ota])

    # Crucial rule: Cross-source observations must NOT be blindly deleted
    assert len(res.clean_observations) == 2
    assert len(res.exact_duplicates) == 0

    # Cross-source group should be identified
    assert len(res.cross_source_groups) == 1
    key = get_cross_source_identity_key(obs_direct)
    assert key in res.cross_source_groups
    assert len(res.cross_source_groups[key]) == 2
