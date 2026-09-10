# pyrefly: ignore [missing-import]
import pytest
from datetime import datetime, date, timezone
from decimal import Decimal
# pyrefly: ignore [missing-import]
from sqlalchemy import text, select
from sqlalchemy.exc import IntegrityError

from backend.app.db.database import engine, SessionLocal
from backend.app.db.base import Base
from backend.app.db.models import (
    City,
    Airport,
    Route,
    Airline,
    DataSource,
    BookingWindow,
    CollectionRun,
    FareObservation,
    DGCATrafficData,
    RouteWeight,
    BasePeriodFare,
    RouteDailySummary,
    IndexDaily,
)
from backend.app.db.seed import seed_booking_windows


@pytest.fixture(scope="function")
def db_session():
    """Provide a transactional test database session with rollback."""
    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)

    yield session

    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()


def test_database_connection(db_session):
    """Verify that database connection executes queries successfully."""
    result = db_session.execute(text("SELECT 1")).scalar()
    assert result == 1


def test_tables_imported():
    """Verify that all 13 tables are registered in SQLAlchemy Base.metadata."""
    expected_tables = {
        "cities",
        "airports",
        "routes",
        "airlines",
        "data_sources",
        "booking_windows",
        "collection_runs",
        "fare_observations",
        "dgca_traffic_data",
        "route_weights",
        "base_period_fares",
        "route_daily_summary",
        "index_daily",
    }
    assert expected_tables.issubset(set(Base.metadata.tables.keys()))


def test_booking_windows_seeded(db_session):
    """Verify that booking windows can be seeded idempotently."""
    seed_booking_windows(db_session)
    windows = db_session.scalars(
        select(BookingWindow).order_by(BookingWindow.display_order)
    ).all()
    codes = [w.window_code for w in windows]
    days = [w.target_advance_days for w in windows]
    assert "T+1" in codes
    assert "T+7" in codes
    assert "T+15" in codes
    assert "T+30" in codes
    assert "T+45" in codes
    assert set(days).issuperset({1, 7, 15, 30, 45})


def test_city_insertion(db_session):
    """Verify that a city master record can be inserted and queried."""
    city = City(
        city_code="TST_DEL",
        city_name="Test Delhi",
        state_name="Delhi",
        is_metro=True,
        is_active=True,
    )
    db_session.add(city)
    db_session.flush()
    assert city.city_id is not None

    queried = db_session.scalar(select(City).where(City.city_code == "TST_DEL"))
    assert queried.city_name == "Test Delhi"


def test_route_references_two_cities(db_session):
    """Verify that a route correctly references two distinct cities."""
    city_a = City(city_code="TST_A", city_name="City A", state_name="State A")
    city_b = City(city_code="TST_B", city_name="City B", state_name="State B")
    db_session.add_all([city_a, city_b])
    db_session.flush()

    route = Route(
        origin_city_id=city_a.city_id,
        destination_city_id=city_b.city_id,
        route_code="TSTA-TSTB",
        is_active=True,
    )
    db_session.add(route)
    db_session.flush()

    assert route.route_id is not None
    assert route.origin_city.city_code == "TST_A"
    assert route.destination_city.city_code == "TST_B"


def test_invalid_self_route_rejected(db_session):
    """Verify that check constraint rejects self-referencing routes (origin == destination)."""
    city = City(city_code="TST_SELF", city_name="Self City", state_name="State")
    db_session.add(city)
    db_session.flush()

    invalid_route = Route(
        origin_city_id=city.city_id,
        destination_city_id=city.city_id,
        route_code="SELF-SELF",
    )
    db_session.add(invalid_route)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_duplicate_directional_route_rejected(db_session):
    """Verify that duplicate directional routes with same origin and destination are rejected."""
    city_a = City(city_code="TST_D1", city_name="City D1", state_name="State")
    city_b = City(city_code="TST_D2", city_name="City D2", state_name="State")
    db_session.add_all([city_a, city_b])
    db_session.flush()

    route1 = Route(
        origin_city_id=city_a.city_id,
        destination_city_id=city_b.city_id,
        route_code="D1-D2-A",
    )
    db_session.add(route1)
    db_session.flush()

    route2 = Route(
        origin_city_id=city_a.city_id,
        destination_city_id=city_b.city_id,
        route_code="D1-D2-B",
    )
    db_session.add(route2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_fare_observation_nullable_scraper_fields(db_session):
    """Verify that fare observations can be stored with nullable scraper fields."""
    city_a = City(city_code="TST_F1", city_name="City F1", state_name="State")
    city_b = City(city_code="TST_F2", city_name="City F2", state_name="State")
    airline = Airline(airline_code="T6E", airline_name="Test IndiGo")
    source = DataSource(
        source_code="TST_MMT", source_name="Test MMT", source_type="OTA"
    )
    seed_booking_windows(db_session)
    window = db_session.scalar(
        select(BookingWindow).where(BookingWindow.window_code == "T+7")
    )
    db_session.add_all([city_a, city_b, airline, source])
    db_session.flush()

    route = Route(
        origin_city_id=city_a.city_id,
        destination_city_id=city_b.city_id,
        route_code="F1-F2",
    )
    db_session.add(route)
    db_session.flush()

    run = CollectionRun(source_id=source.source_id, status="COMPLETED")
    db_session.add(run)
    db_session.flush()

    # Observation with NULL departure/arrival times, fare components, etc.
    obs = FareObservation(
        run_id=run.run_id,
        data_source_id=source.source_id,
        route_id=route.route_id,
        airline_id=airline.airline_id,
        window_id=window.window_id,
        flight_number="6E-101",
        observed_at=datetime.now(timezone.utc),
        travel_date=date(2026, 9, 15),
        advance_days=7,
        scheduled_departure_time=None,
        scheduled_arrival_time=None,
        origin_airport_code=None,
        destination_airport_code=None,
        cabin_class="ECONOMY",
        fare_family=None,
        is_non_stop=True,
        stops=None,
        total_fare=Decimal("4999.00"),
        base_fare=None,
        fuel_surcharge=None,
        taxes_and_fees=None,
        convenience_fee=None,
        currency="INR",
        is_available=True,
        seats_remaining=None,
        quality_status="VALID",
        fingerprint_hash="hash_test_12345",
    )
    db_session.add(obs)
    db_session.flush()

    assert obs.observation_id is not None
    assert obs.scheduled_departure_time is None
    assert obs.base_fare is None
    assert obs.total_fare == Decimal("4999.00")


def test_duplicate_observation_within_same_run_rejected(db_session):
    """Verify that duplicate observations within same run and source are rejected."""
    city_a = City(city_code="TST_U1", city_name="City U1", state_name="State")
    city_b = City(city_code="TST_U2", city_name="City U2", state_name="State")
    airline = Airline(airline_code="TAI", airline_name="Test Air India")
    source = DataSource(
        source_code="TST_AI_DIR", source_name="Test Direct", source_type="AIRLINE_DIRECT"
    )
    seed_booking_windows(db_session)
    window = db_session.scalar(
        select(BookingWindow).where(BookingWindow.window_code == "T+1")
    )
    db_session.add_all([city_a, city_b, airline, source])
    db_session.flush()

    route = Route(
        origin_city_id=city_a.city_id,
        destination_city_id=city_b.city_id,
        route_code="U1-U2",
    )
    db_session.add(route)
    db_session.flush()

    run = CollectionRun(source_id=source.source_id, status="RUNNING")
    db_session.add(run)
    db_session.flush()

    obs1 = FareObservation(
        run_id=run.run_id,
        data_source_id=source.source_id,
        route_id=route.route_id,
        airline_id=airline.airline_id,
        window_id=window.window_id,
        flight_number="AI-202",
        observed_at=datetime.now(timezone.utc),
        travel_date=date(2026, 9, 10),
        advance_days=1,
        total_fare=Decimal("5500.00"),
        fingerprint_hash="duplicate_test_hash",
    )
    db_session.add(obs1)
    db_session.flush()

    # Identical run_id, source_id, and fingerprint_hash
    obs2 = FareObservation(
        run_id=run.run_id,
        data_source_id=source.source_id,
        route_id=route.route_id,
        airline_id=airline.airline_id,
        window_id=window.window_id,
        flight_number="AI-202",
        observed_at=datetime.now(timezone.utc),
        travel_date=date(2026, 9, 10),
        advance_days=1,
        total_fare=Decimal("5500.00"),
        fingerprint_hash="duplicate_test_hash",
    )
    db_session.add(obs2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_same_flight_in_different_runs_allowed(db_session):
    """Verify that observations of the same flight in different runs (or sources) can coexist."""
    city_a = City(city_code="TST_M1", city_name="City M1", state_name="State")
    city_b = City(city_code="TST_M2", city_name="City M2", state_name="State")
    airline = Airline(airline_code="TQP", airline_name="Test Akasa")
    source = DataSource(
        source_code="TST_SRC", source_name="Test Source", source_type="AIRLINE_DIRECT"
    )
    seed_booking_windows(db_session)
    window = db_session.scalar(
        select(BookingWindow).where(BookingWindow.window_code == "T+15")
    )
    db_session.add_all([city_a, city_b, airline, source])
    db_session.flush()

    route = Route(
        origin_city_id=city_a.city_id,
        destination_city_id=city_b.city_id,
        route_code="M1-M2",
    )
    db_session.add(route)
    db_session.flush()

    run1 = CollectionRun(source_id=source.source_id, status="COMPLETED")
    run2 = CollectionRun(source_id=source.source_id, status="COMPLETED")
    db_session.add_all([run1, run2])
    db_session.flush()

    # Morning observation
    obs_morning = FareObservation(
        run_id=run1.run_id,
        data_source_id=source.source_id,
        route_id=route.route_id,
        airline_id=airline.airline_id,
        window_id=window.window_id,
        flight_number="QP-303",
        observed_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
        travel_date=date(2026, 9, 16),
        advance_days=15,
        total_fare=Decimal("4200.00"),
        fingerprint_hash="qp303_morning_hash",
    )

    # Evening observation (price surge to 4800)
    obs_evening = FareObservation(
        run_id=run2.run_id,
        data_source_id=source.source_id,
        route_id=route.route_id,
        airline_id=airline.airline_id,
        window_id=window.window_id,
        flight_number="QP-303",
        observed_at=datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc),
        travel_date=date(2026, 9, 16),
        advance_days=15,
        total_fare=Decimal("4800.00"),
        fingerprint_hash="qp303_evening_hash",
    )

    db_session.add_all([obs_morning, obs_evening])
    db_session.flush()

    assert obs_morning.observation_id is not None
    assert obs_evening.observation_id is not None
    assert obs_morning.total_fare == Decimal("4200.00")
    assert obs_evening.total_fare == Decimal("4800.00")
