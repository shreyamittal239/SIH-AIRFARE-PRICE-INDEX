"""Integration tests for the complete cleaning pipeline using synthetic fixtures."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest

from backend.processing.cleaning.cleaning_pipeline import CleaningPipeline
from backend.processing.cleaning.validators import QualityStatus


def build_synthetic_fixture(
    index: int,
    status_intent: str,
    fare: float = 7500.0,
    flight_num: str = "SG 101",
    route_id: int = 1,
    travel_date: date = date(2026, 9, 15),
    window_id: int = 2,
    run_id: int = 1,
    data_source_id: int = 1,
    fp_hash: str = "fp_default",
) -> SimpleNamespace:
    """Generate synthetic FareObservation fixture representing various data quality conditions."""
    obs = SimpleNamespace(
        observation_id=index,
        run_id=run_id,
        data_source_id=data_source_id,
        route_id=route_id,
        airline_id=1,
        window_id=window_id,
        flight_number=flight_num,
        observed_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        travel_date=travel_date,
        advance_days=7,
        total_fare=Decimal(str(fare)),
        currency="INR",
        is_available=True,
        cabin_class="ECONOMY",
        quality_status="VALID",
        fingerprint_hash=fp_hash,
        scheduled_departure_time=time(10, 0),
        scheduled_arrival_time=time(12, 30),
        origin_airport_code="DEL",
        destination_airport_code="BOM",
        is_non_stop=True,
        stops=0,
        fare_family="SpiceSaver",
        base_fare=None,
        fuel_surcharge=None,
        taxes_and_fees=None,
        convenience_fee=None,
        seats_remaining=None,
    )

    if status_intent == "INVALID_FARE":
        obs.total_fare = Decimal("-100.00")
    elif status_intent == "MISSING":
        obs.flight_number = ""
    elif status_intent == "SOLD_OUT":
        obs.is_available = False
    elif status_intent == "OUTLIER":
        obs.total_fare = Decimal("65000.00")

    return obs


def test_cleaning_pipeline_synthetic_dataset():
    """Verify that cleaning pipeline accurately categorizes and partitions synthetic data."""
    # Cohort of 6 observations on Route 1, Date 2026-09-15:
    # 5 standard fares (6800 - 7400) + 1 OUTLIER (65000)
    cohort_normal = [
        build_synthetic_fixture(i, "VALID", fare=f, flight_num=f"SG 10{i}", fp_hash=f"fp_valid_{i}")
        for i, f in enumerate([6800.0, 6900.0, 7100.0, 7250.0, 7400.0], 1)
    ]
    obs_outlier = build_synthetic_fixture(6, "OUTLIER", flight_num="SG 106", fp_hash="fp_outlier")

    # Anomalies
    obs_invalid = build_synthetic_fixture(7, "INVALID_FARE", flight_num="SG 107", fp_hash="fp_inv")
    obs_missing = build_synthetic_fixture(8, "MISSING", flight_num="", fp_hash="fp_mis")
    obs_sold_out = build_synthetic_fixture(9, "SOLD_OUT", flight_num="SG 109", fp_hash="fp_sold")

    # Duplicate: exact same (run_id, data_source_id, fp_hash) as cohort_normal[0]
    obs_duplicate = build_synthetic_fixture(
        10, "VALID", fare=6800.0, flight_num="SG 101", fp_hash="fp_valid_1"
    )

    synthetic_dataset = cohort_normal + [
        obs_outlier,
        obs_invalid,
        obs_missing,
        obs_sold_out,
        obs_duplicate,
    ]
    initial_count = len(synthetic_dataset)  # 10 records

    pipeline = CleaningPipeline()
    summary = pipeline.clean(synthetic_dataset)

    # 1. Verify non-destructive guarantee (raw list count untouched)
    assert len(synthetic_dataset) == initial_count

    # 2. Verify breakdown counts
    assert summary.total == 10
    assert summary.valid == 5  # The 5 normal cohort observations
    assert summary.missing == 1
    assert summary.invalid_fare == 1
    assert summary.sold_out == 1
    assert summary.duplicate == 1
    assert summary.outlier == 1

    # 3. Verify clean observations collection
    assert len(summary.clean_observations) == 5
    for clean_obs in summary.clean_observations:
        assert clean_obs.quality_status == QualityStatus.VALID.value
        assert clean_obs.total_fare < Decimal("10000.00")

    # 4. Verify flagged observations
    assert len(summary.flagged_observations) == 5
    flagged_statuses = [status for _, status, _ in summary.flagged_observations]
    assert "MISSING" in flagged_statuses
    assert "INVALID_FARE" in flagged_statuses
    assert "SOLD_OUT" in flagged_statuses
    assert "DUPLICATE" in flagged_statuses
    assert "OUTLIER" in flagged_statuses


def test_group_for_representative_fare():
    """Verify clean observations are properly grouped by route + travel_date + window."""
    obs_r1_d1 = build_synthetic_fixture(1, "VALID", route_id=1, travel_date=date(2026, 9, 15), window_id=2)
    obs_r1_d1_alt = build_synthetic_fixture(2, "VALID", route_id=1, travel_date=date(2026, 9, 15), window_id=2)
    obs_r1_d2 = build_synthetic_fixture(3, "VALID", route_id=1, travel_date=date(2026, 9, 22), window_id=3)
    obs_r2_d1 = build_synthetic_fixture(4, "VALID", route_id=2, travel_date=date(2026, 9, 15), window_id=2)

    clean_set = [obs_r1_d1, obs_r1_d1_alt, obs_r1_d2, obs_r2_d1]
    grouped = CleaningPipeline.group_for_representative_fare(clean_set)

    assert len(grouped) == 3
    # Group (route 1, 2026-09-15, window 2) has 2 flights
    assert len(grouped[(1, date(2026, 9, 15), 2)]) == 2
    # Group (route 1, 2026-09-22, window 3) has 1 flight
    assert len(grouped[(1, date(2026, 9, 22), 3)]) == 1
    # Group (route 2, 2026-09-15, window 2) has 1 flight
    assert len(grouped[(2, date(2026, 9, 15), 2)]) == 1


def test_cleaning_pipeline_low_dispersion_integration():
    """TEST 8 — Cleaning pipeline integration with low-dispersion cohort.

    Simulates the Yatra CollectionRun #76 cohort:
    - 22 flights at ₹6,530
    - 2 flights at ₹6,490
    Verifies:
    - raw observations are not mutated
    - cleaning retains all 24 as VALID (0 outliers)
    - RouteDailySummaryService with exclude_outliers=True and False both include all 24
    """
    from backend.processing.aggregation.route_daily_summary_service import RouteDailySummaryService

    fares = [6490.0, 6490.0] + [6530.0] * 22
    observations = [
        build_synthetic_fixture(
            i,
            "VALID",
            fare=f,
            flight_num=f"FL {i}",
            travel_date=date(2026, 9, 15),
            fp_hash=f"fp_low_disp_{i}",
        )
        for i, f in enumerate(fares, 1)
    ]
    initial_count = len(observations)

    pipeline = CleaningPipeline()
    summary = pipeline.clean(observations)

    # 1. Non-destructive check
    assert len(observations) == initial_count

    # 2. Cleaning results: all 24 valid, 0 outliers
    assert summary.total == 24
    assert summary.valid == 24
    assert summary.outlier == 0
    assert len(summary.clean_observations) == 24

    # 3. Downstream aggregation with exclude_outliers=True
    service_excl = RouteDailySummaryService(exclude_outliers=True)
    summaries_excl = service_excl.aggregate_cohorts(observations)
    assert len(summaries_excl) == 1
    assert summaries_excl[0].observations_count == 24
    assert summaries_excl[0].min_fare == Decimal("6490.00")

    # 4. Downstream aggregation with exclude_outliers=False
    service_incl = RouteDailySummaryService(exclude_outliers=False)
    summaries_incl = service_incl.aggregate_cohorts(observations)
    assert len(summaries_incl) == 1
    assert summaries_incl[0].observations_count == 24
    assert summaries_incl[0].min_fare == Decimal("6490.00")

