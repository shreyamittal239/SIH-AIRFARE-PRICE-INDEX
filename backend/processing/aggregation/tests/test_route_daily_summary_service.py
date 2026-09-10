"""Unit and database tests for RouteDailySummary aggregation service."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest
from sqlalchemy import select

from backend.app.db.database import engine, SessionLocal
from backend.app.db.models.route_daily_summary import RouteDailySummary
from backend.app.db.models.airline import Airline
from backend.app.db.models.data_source import DataSource
from backend.app.db.models.route import Route
from backend.app.db.models.city import City
from backend.app.db.seed import seed_booking_windows
from backend.processing.aggregation.reconciliation import CrossSourcePolicy
from backend.processing.aggregation.statistics import RepresentativeFareMetric
from backend.processing.aggregation.route_daily_summary_service import (
    RouteDailySummaryService,
)


@pytest.fixture(scope="function")
def db():
    """Provide a transactional test database session with rollback."""
    conn = engine.connect()
    tx = conn.begin()
    session = SessionLocal(bind=conn)

    seed_booking_windows(session)

    yield session

    session.close()
    if tx.is_active:
        tx.rollback()
    conn.close()


def make_summary_test_obs(
    flight_number: str,
    total_fare: float,
    route_id: int = 1,
    window_id: int = 2,
    observed_at: datetime = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
    travel_date: date = date(2026, 9, 15),
    data_source_id: int = 1,
    airline_id: int = 1,
    departure_time: time = time(19, 55),
    quality_status: str = "VALID",
    is_available: bool = True,
    cabin_class: str = "ECONOMY",
) -> SimpleNamespace:
    """Helper to build test observations for aggregation."""
    return SimpleNamespace(
        flight_number=flight_number,
        total_fare=Decimal(str(total_fare)),
        route_id=route_id,
        window_id=window_id,
        observed_at=observed_at,
        travel_date=travel_date,
        data_source_id=data_source_id,
        airline_id=airline_id,
        scheduled_departure_time=departure_time,
        quality_status=quality_status,
        is_available=is_available,
        cabin_class=cabin_class,
    )


def test_aggregation_grouping_and_date_semantics():
    """Verify grouping by (observation_date, route_id, window_id) and date separation."""
    # Observations on observation_date = 2026-09-08
    # Travel date = 2026-09-15 (T+7, window_id=2)
    obs1 = make_summary_test_obs("SG 101", 7000.0, route_id=1, window_id=2)
    obs2 = make_summary_test_obs("SG 102", 8000.0, route_id=1, window_id=2)

    # Different route (route_id=2)
    obs_diff_route = make_summary_test_obs("SG 201", 6000.0, route_id=2, window_id=2)

    # Different booking window (window_id=3, T+15)
    obs_diff_window = make_summary_test_obs(
        "SG 301", 5000.0, route_id=1, window_id=3, travel_date=date(2026, 9, 23)
    )

    service = RouteDailySummaryService()
    summaries = service.aggregate_cohorts([obs1, obs2, obs_diff_route, obs_diff_window])

    assert len(summaries) == 3

    # Check Route 1, Window 2 summary
    r1_w2 = next(s for s in summaries if s.route_id == 1 and s.window_id == 2)
    assert r1_w2.observation_date == date(2026, 9, 8)
    assert r1_w2.target_travel_date == date(2026, 9, 15)
    assert r1_w2.observations_count == 2
    assert r1_w2.min_fare == Decimal("7000.00")
    assert r1_w2.median_fare == Decimal("7500.00")
    assert r1_w2.mean_fare == Decimal("7500.00")
    # GM of 7000 and 8000 = sqrt(56,000,000) ≈ 7483.31
    assert r1_w2.geometric_mean_fare == Decimal("7483.31")
    assert r1_w2.representative_fare == Decimal("7483.31")


def test_cross_source_reconciliation_in_aggregation():
    """Verify multiple sources for the same flight reconcile before computing route statistics."""
    # Same physical flight (SG 101) from 2 sources
    quote_direct = make_summary_test_obs("SG 101", 7800.0, data_source_id=1)
    quote_ota = make_summary_test_obs("SG 101", 8100.0, data_source_id=2)

    # Second distinct flight (SG 102) from 2 sources
    quote2_direct = make_summary_test_obs("SG 102", 8000.0, data_source_id=1, departure_time=time(22, 0))
    quote2_ota = make_summary_test_obs("SG 102", 7900.0, data_source_id=3, departure_time=time(22, 0))

    service = RouteDailySummaryService(cross_source_policy=CrossSourcePolicy.MIN_VALID_FARE)
    summaries = service.aggregate_cohorts([quote_direct, quote_ota, quote2_direct, quote2_ota])

    assert len(summaries) == 1
    summ = summaries[0]

    # Reconciled flights are SG 101 (₹7,800) and SG 102 (₹7,900)
    # Total flight count must be 2, NOT 4!
    assert summ.observations_count == 2
    assert summ.min_fare == Decimal("7800.00")
    assert summ.median_fare == Decimal("7850.00")
    assert summ.mean_fare == Decimal("7850.00")


def test_ineligible_observations_excluded():
    """Verify invalid, sold out, and duplicate observations are excluded."""
    valid_obs = make_summary_test_obs("SG 101", 7000.0)
    invalid_obs = make_summary_test_obs("SG 102", -500.0, quality_status="INVALID_FARE")
    sold_out_obs = make_summary_test_obs("SG 103", 6500.0, is_available=False, quality_status="SOLD_OUT")
    duplicate_obs = make_summary_test_obs("SG 101", 7000.0, quality_status="DUPLICATE")

    service = RouteDailySummaryService()
    summaries = service.aggregate_cohorts([valid_obs, invalid_obs, sold_out_obs, duplicate_obs])

    assert len(summaries) == 1
    assert summaries[0].observations_count == 1
    assert summaries[0].min_fare == Decimal("7000.00")


def test_outlier_exclusion_policy_toggle():
    """Verify outlier exclusion toggle (exclude_outliers=True vs False)."""
    normal_obs = make_summary_test_obs("SG 101", 7000.0)
    outlier_obs = make_summary_test_obs("SG 102", 65000.0, quality_status="OUTLIER", departure_time=time(22, 0))

    # With default exclude_outliers=True
    service_excl = RouteDailySummaryService(exclude_outliers=True)
    summaries_excl = service_excl.aggregate_cohorts([normal_obs, outlier_obs])
    assert len(summaries_excl) == 1
    assert summaries_excl[0].observations_count == 1
    assert summaries_excl[0].min_fare == Decimal("7000.00")

    # With exclude_outliers=False
    service_incl = RouteDailySummaryService(exclude_outliers=False)
    summaries_incl = service_incl.aggregate_cohorts([normal_obs, outlier_obs])
    assert len(summaries_incl) == 1
    assert summaries_incl[0].observations_count == 2
    assert summaries_incl[0].min_fare == Decimal("7000.00")


def test_representative_fare_metric_selection():
    """Verify representative_fare follows configured metric (Median vs GM)."""
    obs1 = make_summary_test_obs("SG 101", 6000.0)
    obs2 = make_summary_test_obs("SG 102", 8000.0, departure_time=time(21, 0))

    service_med = RouteDailySummaryService(representative_metric=RepresentativeFareMetric.MEDIAN)
    summaries_med = service_med.aggregate_cohorts([obs1, obs2])
    assert summaries_med[0].representative_fare == Decimal("7000.00")

    service_min = RouteDailySummaryService(representative_metric=RepresentativeFareMetric.MINIMUM)
    summaries_min = service_min.aggregate_cohorts([obs1, obs2])
    assert summaries_min[0].representative_fare == Decimal("6000.00")


def test_idempotent_persistence_in_db(db):
    """Test that persisting RouteDailySummary twice updates in-place without duplicate key error."""
    # Ensure reference route exists
    city_a = City(city_code="TST_A", city_name="City A", state_name="State A")
    city_b = City(city_code="TST_B", city_name="City B", state_name="State B")
    db.add_all([city_a, city_b])
    db.flush()

    route = Route(origin_city_id=city_a.city_id, destination_city_id=city_b.city_id, route_code="TSTA-TSTB")
    db.add(route)
    db.flush()

    obs1 = make_summary_test_obs("SG 101", 7000.0, route_id=route.route_id, window_id=2)
    obs2 = make_summary_test_obs("SG 102", 8000.0, route_id=route.route_id, window_id=2, departure_time=time(21, 0))

    service = RouteDailySummaryService(db=db)

    # First run: inserts summary
    res1 = service.process_and_persist([obs1, obs2])
    assert res1.summaries_created == 1
    assert res1.summaries_updated == 0

    # Second run with updated price: updates record idempotently
    obs2_updated = make_summary_test_obs("SG 102", 9000.0, route_id=route.route_id, window_id=2, departure_time=time(21, 0))
    res2 = service.process_and_persist([obs1, obs2_updated])
    assert res2.summaries_created == 0
    assert res2.summaries_updated == 1

    # Verify database has exactly 1 summary record for this (obs_date, route, window)
    stmt = select(RouteDailySummary).where(
        RouteDailySummary.observation_date == date(2026, 9, 8),
        RouteDailySummary.route_id == route.route_id,
        RouteDailySummary.window_id == 2,
    )
    records = db.scalars(stmt).all()
    assert len(records) == 1
    # Updated GM of 7000 and 9000 = sqrt(63,000,000) ≈ 7937.25
    assert records[0].geometric_mean_fare == Decimal("7937.25")


def test_travel_date_consistency_all_sih_windows():
    """Verify exact calendar date consistency across all standard SIH booking windows."""
    obs_date = date(2026, 9, 8)
    obs_dt = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)

    # All standard SIH booking windows:
    # T+1  -> 2026-09-09
    # T+7  -> 2026-09-15
    # T+15 -> 2026-09-23
    # T+30 -> 2026-10-08
    # T+45 -> 2026-10-23
    sih_cases = [
        (1, 1, date(2026, 9, 9)),
        (2, 7, date(2026, 9, 15)),
        (3, 15, date(2026, 9, 23)),
        (4, 30, date(2026, 10, 8)),
        (5, 45, date(2026, 10, 23)),
    ]

    observations = []
    for wid, adv, expected_date in sih_cases:
        obs = make_summary_test_obs(
            flight_number=f"SG {wid}01",
            total_fare=5000.0 + wid * 500,
            route_id=1,
            window_id=wid,
            observed_at=obs_dt,
            travel_date=expected_date,
        )
        observations.append(obs)

    service = RouteDailySummaryService()
    eligible = service.filter_eligible_observations(observations)
    assert len(eligible) == 5

    summaries = service.aggregate_cohorts(observations)
    assert len(summaries) == 5

    for wid, adv, expected_date in sih_cases:
        summ = next(s for s in summaries if s.window_id == wid)
        assert summ.observation_date == obs_date
        assert summ.target_travel_date == expected_date
        assert summ.observations_count == 1


def test_travel_date_inconsistency_rejected():
    """Verify observations with inconsistent travel dates are rejected from aggregation."""
    obs_dt = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)

    # Valid observation: T+7 with expected travel date 2026-09-15
    valid_obs = make_summary_test_obs(
        flight_number="SG 101",
        total_fare=7000.0,
        window_id=2,
        observed_at=obs_dt,
        travel_date=date(2026, 9, 15),
    )

    # Inconsistent observation: T+7 window, but travel_date is 2026-09-20 (5 days late)
    inconsistent_obs = make_summary_test_obs(
        flight_number="SG 102",
        total_fare=7500.0,
        window_id=2,
        observed_at=obs_dt,
        travel_date=date(2026, 9, 20),  # INCORRECT for T+7
    )

    service = RouteDailySummaryService()

    # Inconsistent observation must be excluded during eligibility filtering
    eligible = service.filter_eligible_observations([valid_obs, inconsistent_obs])
    assert len(eligible) == 1
    assert eligible[0].flight_number == "SG 101"

    # Aggregation must only include the consistent observation
    summaries = service.aggregate_cohorts([valid_obs, inconsistent_obs])
    assert len(summaries) == 1
    assert summaries[0].observations_count == 1
    assert summaries[0].target_travel_date == date(2026, 9, 15)


def test_cohort_safety_conflicting_travel_dates_rejected():
    """Ensure aggregation cannot combine different target travel dates into one RouteDailySummary row."""
    obs_dt = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)

    # Direct manual cohort bypass test: two observations with identical (obs_date, route, window)
    # but conflicting travel_dates
    obs1 = make_summary_test_obs(
        flight_number="SG 101",
        total_fare=7000.0,
        route_id=1,
        window_id=2,
        observed_at=obs_dt,
        travel_date=date(2026, 9, 15),
    )
    obs2 = make_summary_test_obs(
        flight_number="SG 102",
        total_fare=8000.0,
        route_id=1,
        window_id=2,
        observed_at=obs_dt,
        travel_date=date(2026, 9, 16),  # Conflicting travel date!
    )

    service = RouteDailySummaryService()

    # When both are passed directly into cohort aggregation (simulating a bypass of filter):
    # Cohort safety MUST detect the conflict and reject the cohort completely.
    summaries = service.aggregate_cohorts([obs1, obs2])
    # obs2 is rejected by filter_eligible_observations anyway, leaving obs1
    assert len(summaries) == 1
    assert summaries[0].target_travel_date == date(2026, 9, 15)
    assert summaries[0].observations_count == 1


def test_cohort_safety_multiple_dates_in_cohort_rejected_completely(monkeypatch):
    """Verify that if two differing travel dates exist in the cohort map, the cohort is rejected completely."""
    obs_dt = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    obs1 = make_summary_test_obs(
        flight_number="SG 101",
        total_fare=7000.0,
        route_id=1,
        window_id=2,
        observed_at=obs_dt,
        travel_date=date(2026, 9, 15),
    )
    obs2 = make_summary_test_obs(
        flight_number="SG 102",
        total_fare=8000.0,
        route_id=1,
        window_id=2,
        observed_at=obs_dt,
        travel_date=date(2026, 9, 16),
    )

    service = RouteDailySummaryService()
    # Bypass filter_eligible_observations to test the cohort-level safety net directly
    monkeypatch.setattr(service, "filter_eligible_observations", lambda obs: obs)

    summaries = service.aggregate_cohorts([obs1, obs2])
    # The conflicting cohort MUST be completely rejected (0 summaries produced)
    assert len(summaries) == 0
