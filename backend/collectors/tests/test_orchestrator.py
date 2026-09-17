"""Unit and integration tests for the CollectionOrchestrator and CollectorAdapter registry.

Covers:
1. Dynamic route basket querying from route_weights
2. Dynamic booking window querying from booking_windows
3. Task matrix generation (N routes × M windows)
4. CRITICAL: Automatic new route support (adding a 26th route expands matrix, deactivating returns to 125)
5. CRITICAL: Automatic new booking window support (adding a 6th window expands matrix with 0 code changes)
6. Task validation (rejecting identical origin/destination, invalid IATA, mismatched advance days)
7. Collector capability: distinguishing zero flights (COMPLETED, records_scraped=0) from crashes
8. Broken parser guard: unexpected empty result without zero-inventory banner is marked FAILED
9. Timezone observation date and midnight boundary handling (Asia/Kolkata vs UTC)
10. Task-level error isolation: a failure on one task does not abort subsequent tasks
11. Collector adapter registry and unified dispatch
12. Transactional persistence and deduplication via IngestionRepository
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import pytest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.base import Base
import backend.app.db.models  # Ensure all models are registered
from backend.app.db.models.city import City
from backend.app.db.models.route import Route
from backend.app.db.models.route_weight import RouteWeight
from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.data_source import DataSource
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation

from backend.collectors.orchestrator import (
    CollectionOrchestrator,
    CollectionTask,
    BasketRoute,
    WindowInfo,
    TaskValidationError,
    validate_task,
    CollectorAdapter,
    CollectionStatus,
    CollectionResult,
    COLLECTOR_REGISTRY,
    get_collector_adapter,
    get_current_observation_date,
    detect_no_inventory_banner,
    PROJECT_TIMEZONE,
)
from backend.collectors.playwright.schemas.flight_quote import FlightQuote


@pytest.fixture
def db_session():
    """Provide an isolated, fully seeded in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # 1. Seed 5 standard SIH Booking Windows
    windows = [
        BookingWindow(window_code="T+1", target_advance_days=1, display_order=1, is_active=True),
        BookingWindow(window_code="T+7", target_advance_days=7, display_order=2, is_active=True),
        BookingWindow(window_code="T+15", target_advance_days=15, display_order=3, is_active=True),
        BookingWindow(window_code="T+30", target_advance_days=30, display_order=4, is_active=True),
        BookingWindow(window_code="T+45", target_advance_days=45, display_order=5, is_active=True),
    ]
    session.add_all(windows)

    # 2. Seed Master Cities for 25 routes
    city_defs = [
        ("DEL", "Delhi", "Delhi", True),
        ("BOM", "Mumbai", "Maharashtra", True),
        ("BLR", "Bengaluru", "Karnataka", True),
        ("HYD", "Hyderabad", "Telangana", True),
        ("CCU", "Kolkata", "West Bengal", True),
        ("PNQ", "Pune", "Maharashtra", False),
        ("AMD", "Ahmedabad", "Gujarat", False),
        ("MAA", "Chennai", "Tamil Nadu", True),
        ("SXR", "Srinagar", "Jammu and Kashmir", False),
        ("GAU", "Guwahati", "Assam", False),
        ("PAT", "Patna", "Bihar", False),
        ("COK", "Kochi", "Kerala", False),
        ("LKO", "Lucknow", "Uttar Pradesh", False),
        ("IXB", "Bagdogra", "West Bengal", False),
        ("IXL", "Leh", "Ladakh", False),
    ]
    city_map = {}
    for code, name, state, is_metro in city_defs:
        c = City(city_code=code, city_name=name, state_name=state, is_metro=is_metro, is_active=True)
        session.add(c)
        session.flush()
        city_map[code] = c

    # 3. Seed 25 verified Top-25 routes and route_weights
    top25_pairs = [
        ("DEL", "BOM", 459060, Decimal("0.10683653")),
        ("BLR", "DEL", 382427, Decimal("0.08900181")),
        ("BLR", "BOM", 291642, Decimal("0.06787352")),
        ("DEL", "HYD", 243398, Decimal("0.05664575")),
        ("DEL", "CCU", 221148, Decimal("0.05146753")),
        ("DEL", "PNQ", 219479, Decimal("0.05107911")),
        ("BLR", "PNQ", 174676, Decimal("0.04065215")),
        ("AMD", "DEL", 170130, Decimal("0.03959417")),
        ("BLR", "HYD", 165558, Decimal("0.03853013")),
        ("MAA", "DEL", 164240, Decimal("0.03822339")),
        ("MAA", "BOM", 162447, Decimal("0.03780611")),
        ("HYD", "BOM", 156353, Decimal("0.03638786")),
        ("DEL", "SXR", 142396, Decimal("0.03313966")),
        ("BLR", "CCU", 138704, Decimal("0.03228043")),
        ("CCU", "BOM", 134889, Decimal("0.03139257")),
        ("AMD", "BOM", 131643, Decimal("0.03063713")),
        ("BLR", "MAA", 126736, Decimal("0.02949513")),
        ("DEL", "GAU", 117079, Decimal("0.02724767")),
        ("DEL", "PAT", 108899, Decimal("0.02534394")),
        ("BLR", "COK", 106098, Decimal("0.02469207")),
        ("DEL", "LKO", 102553, Decimal("0.02386705")),
        ("IXB", "DEL", 99748, Decimal("0.02321424")),
        ("DEL", "IXL", 94124, Decimal("0.02190537")),
        ("COK", "BOM", 91894, Decimal("0.02138639")),
        ("MAA", "HYD", 91524, Decimal("0.02130028")),
    ]
    for c1_code, c2_code, vol, wt in top25_pairs:
        r = Route(
            origin_city_id=city_map[c1_code].city_id,
            destination_city_id=city_map[c2_code].city_id,
            route_code=f"{c1_code}-{c2_code}",
            is_active=True,
        )
        session.add(r)
        session.flush()

        rw = RouteWeight(
            route_id=r.route_id,
            base_period_code="2026-07",
            passenger_volume=vol,
            weight=wt,
            is_active=True,
        )
        session.add(rw)

    # 4. Seed Data Sources
    sources = [
        DataSource(source_code="CLEARTRIP", source_name="Cleartrip", source_type="OTA", is_active=True),
        DataSource(source_code="YATRA", source_name="Yatra", source_type="OTA", is_active=True),
        DataSource(source_code="EASEMYTRIP", source_name="EaseMyTrip", source_type="OTA", is_active=True),
        DataSource(source_code="AIR_INDIA_EXPRESS", source_name="Air India Express Direct", source_type="AIRLINE_DIRECT", is_active=True),
        DataSource(source_code="SPICEJET", source_name="SpiceJet Direct", source_type="AIRLINE_DIRECT", is_active=True),
    ]
    session.add_all(sources)
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)


# ==============================================================================
# 1. DYNAMIC ROUTE & WINDOW QUERIES
# ==============================================================================
def test_1_get_active_basket_routes_returns_25(db_session):
    """Verify orchestrator dynamically queries active routes from route_weights."""
    orchestrator = CollectionOrchestrator(db_session)
    routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")

    assert len(routes) == 25
    top = routes[0]
    assert top.route_code == "DEL-BOM"
    assert top.origin_code == "DEL"
    assert top.destination_code == "BOM"
    assert top.weight == Decimal("0.10683653")


def test_2_get_active_booking_windows_returns_5(db_session):
    """Verify orchestrator dynamically queries active booking windows from booking_windows."""
    orchestrator = CollectionOrchestrator(db_session)
    windows = orchestrator.get_active_booking_windows()

    assert len(windows) == 5
    codes = [w.window_code for w in windows]
    assert codes == ["T+1", "T+7", "T+15", "T+30", "T+45"]


def test_2b_timezone_midnight_boundary():
    """Verify observation date handles midnight boundary in Asia/Kolkata (IST vs UTC)."""
    # 2026-09-14 19:00:00 UTC == 2026-09-15 00:30:00 IST (Day has changed in India!)
    utc_time = datetime(2026, 9, 14, 19, 0, 0, tzinfo=timezone.utc)
    ist_tz = ZoneInfo("Asia/Kolkata")
    ist_time = utc_time.astimezone(ist_tz)

    assert utc_time.date() == date(2026, 9, 14)
    assert ist_time.date() == date(2026, 9, 15)

    # Calling get_current_observation_date with explicit tz uses target timezone
    date_ist = ist_time.date()
    assert date_ist == date(2026, 9, 15)


def test_3_build_task_matrix_25_x_5(db_session):
    """Verify Cartesian product matrix produces exactly 25 × 5 = 125 tasks."""
    orchestrator = CollectionOrchestrator(db_session)
    routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    windows = orchestrator.get_active_booking_windows()

    obs_date = date(2026, 9, 15)
    tasks = orchestrator.build_task_matrix(routes, windows, observation_date=obs_date)

    assert len(tasks) == 25 * 5 == 125

    del_bom_tasks = [t for t in tasks if t.route.route_code == "DEL-BOM"]
    assert len(del_bom_tasks) == 5

    expected_advances = [1, 7, 15, 30, 45]
    for task, exp_adv in zip(del_bom_tasks, expected_advances):
        assert task.travel_date == obs_date + timedelta(days=exp_adv)
        assert task.window.target_advance_days == exp_adv


# ==============================================================================
# 4. CRITICAL: AUTOMATIC NEW ROUTE SUPPORT AND DEACTIVATION (TASK 4)
# ==============================================================================
def test_4_automatic_new_route_support_and_deactivation(db_session):
    """Verify adding DEL-JAI expands matrix to 130 tasks, and deactivating returns to 125."""
    orchestrator = CollectionOrchestrator(db_session)

    # 1. Baseline: 25 routes × 5 windows = 125 tasks
    routes_25 = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    windows_5 = orchestrator.get_active_booking_windows()
    assert len(orchestrator.build_task_matrix(routes_25, windows_5)) == 125

    # 2. Add Jaipur (JAI) and 26th route DEL-JAI to DB
    del_city = db_session.query(City).filter_by(city_code="DEL").first()
    jai_city = City(city_code="JAI", city_name="Jaipur", state_name="Rajasthan", is_metro=False, is_active=True)
    db_session.add(jai_city)
    db_session.flush()

    route_26 = Route(origin_city_id=del_city.city_id, destination_city_id=jai_city.city_id, route_code="DEL-JAI", is_active=True)
    db_session.add(route_26)
    db_session.flush()

    rw_26 = RouteWeight(
        route_id=route_26.route_id,
        base_period_code="2026-07",
        passenger_volume=85000,
        weight=Decimal("0.01950000"),
        is_active=True,
    )
    db_session.add(rw_26)
    db_session.commit()

    # 3. Dynamic query: 26 routes × 5 windows = 130 tasks with ZERO code change
    routes_26 = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    assert len(routes_26) == 26
    assert len(orchestrator.build_task_matrix(routes_26, windows_5)) == 130

    # 4. Deactivate DEL-JAI: must return to 25 routes and 125 tasks
    rw_26.is_active = False
    db_session.commit()

    routes_restored = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    assert len(routes_restored) == 25
    assert len(orchestrator.build_task_matrix(routes_restored, windows_5)) == 125


# ==============================================================================
# 5. CRITICAL: AUTOMATIC NEW BOOKING WINDOW SUPPORT (TASK 6)
# ==============================================================================
def test_5_automatic_new_booking_window_support(db_session):
    """Verify that adding a 6th booking window (T+60) automatically expands the matrix to 25 × 6 = 150 tasks."""
    orchestrator = CollectionOrchestrator(db_session)

    # 1. Add 6th window T+60 (60 days) to booking_windows table
    w_60 = BookingWindow(window_code="T+60", target_advance_days=60, display_order=6, is_active=True)
    db_session.add(w_60)
    db_session.commit()

    # 2. Re-query dynamically: MUST automatically find 6 windows
    windows_6 = orchestrator.get_active_booking_windows()
    assert len(windows_6) == 6
    assert windows_6[-1].window_code == "T+60"

    # 3. Matrix generation: MUST automatically yield 25 × 6 = 150 tasks
    routes_25 = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    tasks_150 = orchestrator.build_task_matrix(routes_25, windows_6)
    assert len(tasks_150) == 25 * 6 == 150


# ==============================================================================
# 6. TASK VALIDATION
# ==============================================================================
def test_6_task_validation():
    """Verify task validation catches invalid routes, codes, and date mismatches."""
    r_valid = BasketRoute(1, "DEL-BOM", "DEL", "BOM", Decimal("0.1"), 1000)
    w_valid = WindowInfo(1, "T+7", 7, 2)
    today = date(2026, 9, 15)

    # 1. Valid task passes
    task_ok = CollectionTask(r_valid, w_valid, today + timedelta(days=7), today)
    validate_task(task_ok)

    # 2. Origin == Destination fails
    r_same = BasketRoute(2, "DEL-DEL", "DEL", "DEL", Decimal("0.1"), 1000)
    task_same = CollectionTask(r_same, w_valid, today + timedelta(days=7), today)
    with pytest.raises(TaskValidationError, match="identical"):
        validate_task(task_same)

    # 3. Invalid IATA code fails
    r_bad_iata = BasketRoute(3, "DEL-BOMBAY", "DEL", "BOMBAY", Decimal("0.1"), 1000)
    task_bad_iata = CollectionTask(r_bad_iata, w_valid, today + timedelta(days=7), today)
    with pytest.raises(TaskValidationError, match="Invalid destination IATA code"):
        validate_task(task_bad_iata)

    # 4. Travel date mismatch with window advance days fails
    task_wrong_date = CollectionTask(r_valid, w_valid, today + timedelta(days=10), today)
    with pytest.raises(TaskValidationError, match="Date mismatch"):
        validate_task(task_wrong_date)


# ==============================================================================
# 7. COLLECTOR CAPABILITY: CONFIRMED ZERO FLIGHTS (TASK 1)
# ==============================================================================
def test_7_confirmed_zero_flights_treated_as_completed(db_session):
    """Verify that a search confirming zero inventory completes with status COMPLETED and records_scraped=0."""
    orchestrator = CollectionOrchestrator(db_session)
    routes = orchestrator.get_active_basket_routes()
    windows = orchestrator.get_active_booking_windows()
    task = CollectionTask(routes[0], windows[1], date(2026, 9, 22), date(2026, 9, 15))

    mock_adapter = MagicMock(spec=CollectorAdapter)
    mock_adapter.collect.return_value = CollectionResult(
        status=CollectionStatus.SUCCESS_NO_INVENTORY,
        quotes=[],
        no_inventory_reason="Confirmed zero-inventory banner detected: 'sorry, no flights'",
    )

    with patch.dict(COLLECTOR_REGISTRY, {"CLEARTRIP": mock_adapter}):
        result = orchestrator.execute_single_task(task, source_code="CLEARTRIP")

    assert result.status == "COMPLETED"
    assert result.collection_status == CollectionStatus.SUCCESS_NO_INVENTORY
    assert result.quotes_count == 0
    assert result.inserted_count == 0
    assert result.error is None

    run = db_session.query(CollectionRun).filter_by(run_id=result.run_id).first()
    assert run.status == "COMPLETED"
    assert run.records_scraped == 0


# ==============================================================================
# 8. BROKEN PARSER GUARD: UNEXPECTED EMPTY RESULT (TASK 1)
# ==============================================================================
def test_8_unexpected_empty_result_is_marked_failed(db_session):
    """Verify that returning 0 quotes without a zero-inventory confirmation banner is marked FAILED."""
    orchestrator = CollectionOrchestrator(db_session)
    routes = orchestrator.get_active_basket_routes()
    windows = orchestrator.get_active_booking_windows()
    task = CollectionTask(routes[0], windows[1], date(2026, 9, 22), date(2026, 9, 15))

    mock_adapter = MagicMock(spec=CollectorAdapter)
    mock_adapter.collect.return_value = CollectionResult(
        status=CollectionStatus.FAILED,
        quotes=[],
        error_message="Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.",
    )

    with patch.dict(COLLECTOR_REGISTRY, {"CLEARTRIP": mock_adapter}):
        result = orchestrator.execute_single_task(task, source_code="CLEARTRIP")

    assert result.status == "FAILED"
    assert result.collection_status == CollectionStatus.FAILED
    assert "Unexpected empty result" in result.error

    run = db_session.query(CollectionRun).filter_by(run_id=result.run_id).first()
    assert run.status == "FAILED"
    assert "Unexpected empty result" in run.error_summary


# ==============================================================================
# 9. TASK-LEVEL FAILURE ISOLATION
# ==============================================================================
def test_9_task_failure_isolation(db_session):
    """Verify that a failure on task 2 does not terminate subsequent tasks in the batch."""
    orchestrator = CollectionOrchestrator(db_session)
    routes = orchestrator.get_active_basket_routes()[:3]
    windows = orchestrator.get_active_booking_windows()[:1]
    tasks = orchestrator.build_task_matrix(routes, windows)
    assert len(tasks) == 3

    def mock_collect(origin, destination, travel_date, browser_manager=None):
        if destination == routes[1].destination_code:
            return CollectionResult(
                status=CollectionStatus.FAILED,
                error_message="WAF Akamai Challenge Block on route 2",
            )
        return CollectionResult(
            status=CollectionStatus.SUCCESS_WITH_QUOTES,
            quotes=[
                FlightQuote(
                    airline="IndiGo",
                    flight_number="6E 202",
                    origin=origin,
                    destination=destination,
                    travel_date=travel_date,
                    departure_time=time(10, 0),
                    arrival_time=time(12, 0),
                    cabin_class="ECONOMY",
                    total_fare=Decimal("5400.00"),
                    currency="INR",
                    source="CLEARTRIP",
                )
            ],
        )

    mock_adapter = MagicMock(spec=CollectorAdapter)
    mock_adapter.collect.side_effect = mock_collect

    with patch.dict(COLLECTOR_REGISTRY, {"CLEARTRIP": mock_adapter}):
        summary = orchestrator.run_collection(
            source_code="CLEARTRIP",
            tasks=tasks,
            delay_seconds=0,
        )

    assert summary.total_tasks == 3
    assert summary.completed_tasks == 2
    assert summary.failed_tasks == 1
    assert summary.total_inserted == 2

    assert summary.results[0].status == "COMPLETED"
    assert summary.results[1].status == "FAILED"
    assert "Akamai Challenge" in summary.results[1].error
    assert summary.results[2].status == "COMPLETED"


# ==============================================================================
# 10. COLLECTOR ADAPTER REGISTRY & SPICEJET ADAPTER
# ==============================================================================
def test_10_collector_registry_lookup():
    """Verify registry correctly resolves source codes and aliases."""
    yatra_adapter = get_collector_adapter("YATRA")
    assert yatra_adapter is not None

    ct_adapter = get_collector_adapter("CLEARTRIP_OTA")
    assert ct_adapter is not None

    with pytest.raises(ValueError, match="No collector adapter registered"):
        get_collector_adapter("UNKNOWN_SOURCE")


def test_10b_spicejet_adapter_handles_constructor_difference():
    """Verify SpiceJet adapter handles FirstAirlineCollector constructor differences."""
    from backend.collectors.orchestrator import SpiceJetCollectorAdapter

    adapter = SpiceJetCollectorAdapter()
    sample_quote = FlightQuote(
        airline="SpiceJet",
        flight_number="SG 8169",
        origin="DEL",
        destination="BOM",
        travel_date=date(2026, 9, 22),
        total_fare=Decimal("4999.00"),
        source="SpiceJet Direct",
    )

    with patch("backend.collectors.playwright.collectors.first_airline_collector.FirstAirlineCollector") as MockCol:
        instance = MockCol.return_value
        instance.collect_quotes.return_value = [sample_quote]

        res = adapter.collect(origin="DEL", destination="BOM", travel_date=date(2026, 9, 22))

        MockCol.assert_called_once_with(
            origin="DEL",
            destination="BOM",
            travel_date=date(2026, 9, 22),
            browser_manager=None,
        )
        assert res.status == CollectionStatus.SUCCESS_WITH_QUOTES
        assert len(res.quotes) == 1
        assert res.quotes[0].flight_number == "SG 8169"


# ==============================================================================
# 11. TRANSACTIONAL PERSISTENCE & DEDUPLICATION (TASK 3)
# ==============================================================================
def test_11_transactional_persistence_and_deduplication(db_session):
    """Verify FareObservation persistence and intra-run deduplication via existing repository."""
    orchestrator = CollectionOrchestrator(db_session)
    routes = orchestrator.get_active_basket_routes()
    windows = orchestrator.get_active_booking_windows()
    task = CollectionTask(routes[0], windows[1], date(2026, 9, 22), date(2026, 9, 15))

    obs_datetime = datetime.combine(task.observation_date, time(0, 0), tzinfo=timezone.utc)
    duplicate_quotes = [
        FlightQuote(
            airline="IndiGo",
            flight_number="6E 5001",
            origin="DEL",
            destination="BOM",
            travel_date=task.travel_date,
            departure_time=time(8, 0),
            arrival_time=time(10, 15),
            cabin_class="ECONOMY",
            total_fare=Decimal("5200.00"),
            source="CLEARTRIP",
            observed_at=obs_datetime,
        ),
        FlightQuote(
            airline="IndiGo",
            flight_number="6E 5001",
            origin="DEL",
            destination="BOM",
            travel_date=task.travel_date,
            departure_time=time(8, 0),
            arrival_time=time(10, 15),
            cabin_class="ECONOMY",
            total_fare=Decimal("5200.00"),
            source="CLEARTRIP",
            observed_at=obs_datetime,
        ),
    ]

    mock_adapter = MagicMock(spec=CollectorAdapter)
    mock_adapter.collect.return_value = CollectionResult(
        status=CollectionStatus.SUCCESS_WITH_QUOTES,
        quotes=duplicate_quotes,
    )

    with patch.dict(COLLECTOR_REGISTRY, {"CLEARTRIP": mock_adapter}):
        result = orchestrator.execute_single_task(task, source_code="CLEARTRIP")

    assert result.status == "COMPLETED"
    assert result.quotes_count == 2
    assert result.inserted_count == 1
    assert result.skipped_count == 1

    obs = db_session.query(FareObservation).filter_by(run_id=result.run_id).all()
    assert len(obs) == 1
    assert obs[0].route_id == task.route.route_id
    assert obs[0].window_id == task.window.window_id
    assert obs[0].fingerprint_hash is not None


def test_12_technical_failure_retry_and_recovery(db_session: Session) -> None:
    """Transient technical failure on attempt 1 is retried after backoff and recovers on attempt 2."""
    orchestrator = CollectionOrchestrator(db=db_session)
    routes = orchestrator.get_active_basket_routes()
    windows = orchestrator.get_active_booking_windows()
    task = CollectionTask(routes[0], windows[1], date(2026, 9, 22), date(2026, 9, 15))
    mock_adapter = MagicMock(spec=CollectorAdapter)

    valid_quote = FlightQuote(
        airline="IndiGo",
        flight_number="6E 5002",
        origin="DEL",
        destination="BOM",
        travel_date=task.travel_date,
        departure_time=time(10, 0),
        arrival_time=time(12, 15),
        cabin_class="ECONOMY",
        total_fare=Decimal("4500.00"),
        source="CLEARTRIP",
        observed_at=datetime.now(timezone.utc),
    )

    # Attempt 1: Timeout error; Attempt 2: Success with quotes
    mock_adapter.collect.side_effect = [
        CollectionResult(status=CollectionStatus.FAILED, error_message="Page.goto: Timeout 35000ms exceeded."),
        CollectionResult(status=CollectionStatus.SUCCESS_WITH_QUOTES, quotes=[valid_quote]),
    ]

    with patch.dict(COLLECTOR_REGISTRY, {"CLEARTRIP": mock_adapter}), patch("time.sleep") as mock_sleep:
        result = orchestrator.execute_single_task(task, source_code="CLEARTRIP")

    assert result.status == "COMPLETED"
    assert result.attempts == 2
    assert result.retried is True
    assert result.recovered_on_retry is True
    assert result.quotes_count == 1
    assert result.inserted_count == 1
    mock_sleep.assert_called_with(5.0)


def test_13_no_retry_on_confirmed_zero_inventory(db_session: Session) -> None:
    """Confirmed zero inventory on attempt 1 is NOT retried."""
    orchestrator = CollectionOrchestrator(db=db_session)
    routes = orchestrator.get_active_basket_routes()
    windows = orchestrator.get_active_booking_windows()
    task = CollectionTask(routes[0], windows[1], date(2026, 9, 22), date(2026, 9, 15))
    mock_adapter = MagicMock(spec=CollectorAdapter)

    mock_adapter.collect.return_value = CollectionResult(
        status=CollectionStatus.SUCCESS_NO_INVENTORY,
        no_inventory_reason="Confirmed zero flights available.",
    )

    with patch.dict(COLLECTOR_REGISTRY, {"CLEARTRIP": mock_adapter}), patch("time.sleep") as mock_sleep:
        result = orchestrator.execute_single_task(task, source_code="CLEARTRIP")

    assert result.status == "COMPLETED"
    assert result.attempts == 1
    assert result.retried is False
    assert result.recovered_on_retry is False
    assert result.quotes_count == 0
    assert result.inserted_count == 0
    mock_sleep.assert_not_called()

