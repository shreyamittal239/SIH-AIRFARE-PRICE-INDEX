"""Unit and database tests for NationalIndex calculation service."""

from datetime import date, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select

from backend.app.db.database import engine, SessionLocal
from backend.app.db.models.index_daily import IndexDaily
from backend.app.db.models.route_weight import RouteWeight
from backend.app.db.models.route import Route
from backend.app.db.models.city import City
from backend.app.db.seed import seed_booking_windows
from backend.processing.aggregation.national_index_service import NationalIndexService

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

def setup_basic_routes(db, route_codes=["R1", "R2"]):
    """Helper to build unique routes for testing."""
    routes = []
    for i, code in enumerate(route_codes):
        city_a = City(city_code=f"A{i}", city_name=f"City A{i}", state_name="State A")
        city_b = City(city_code=f"B{i}", city_name=f"City B{i}", state_name="State B")
        db.add_all([city_a, city_b])
        db.flush()

        r = Route(origin_city_id=city_a.city_id, destination_city_id=city_b.city_id, route_code=code)
        db.add(r)
        routes.append(r)
    db.flush()
    return routes

def test_national_index_all_routes_available(db):
    """Test 1: All configured routes have indices. Standard weighted average."""
    routes = setup_basic_routes(db)
    base_period = "TEST-2026"
    index_date = date(2026, 9, 1)

    # Route 1: Index 100, Weight 0.6
    # Route 2: Index 110, Weight 0.4
    # Expected: (100 * 0.6) + (110 * 0.4) = 60 + 44 = 104.0

    # Add weights
    db.add(RouteWeight(route_id=routes[0].route_id, base_period_code=base_period, weight=Decimal("0.6"), passenger_volume=1000, is_active=True))
    db.add(RouteWeight(route_id=routes[1].route_id, base_period_code=base_period, weight=Decimal("0.4"), passenger_volume=1000, is_active=True))

    # Add ROUTE_LEVEL indices
    db.add(IndexDaily(index_date=index_date, base_period_code=base_period, index_level="ROUTE_LEVEL",
                      route_id=routes[0].route_id, window_id=None, index_value=Decimal("100.0000"),
                      price_relative=Decimal("100.0000"), formula_type="JEVONS", routes_included=1))
    db.add(IndexDaily(index_date=index_date, base_period_code=base_period, index_level="ROUTE_LEVEL",
                      route_id=routes[1].route_id, window_id=None, index_value=Decimal("110.0000"),
                      price_relative=Decimal("110.0000"), formula_type="JEVONS", routes_included=1))
    db.commit()

    service = NationalIndexService(db)
    service.compute_national_indices(base_period)

    stmt = select(IndexDaily).where(IndexDaily.index_level == "NATIONAL_COMPOSITE", IndexDaily.index_date == index_date)
    res = db.scalar(stmt)
    assert res is not None
    assert res.index_value == Decimal("104.0000")
    assert res.routes_included == 2
    assert res.route_id is None
    assert res.window_id is None

def test_national_index_weight_normalization(db):
    """Test 2: Weights don't sum to 1. Verify they are renormalized."""
    routes = setup_basic_routes(db)
    base_period = "TEST-2026"
    index_date = date(2026, 9, 1)

    # Route 1: Index 100, Weight 0.5
    # Route 2: Index 110, Weight 0.3
    # Sum weights = 0.8
    # Normalized: 0.5/0.8 = 0.625, 0.3/0.8 = 0.375
    # Expected: (100 * 0.625) + (110 * 0.375) = 62.5 + 41.25 = 103.75

    db.add(RouteWeight(route_id=routes[0].route_id, base_period_code=base_period, weight=Decimal("0.5"), passenger_volume=1000, is_active=True))
    db.add(RouteWeight(route_id=routes[1].route_id, base_period_code=base_period, weight=Decimal("0.3"), passenger_volume=1000, is_active=True))

    db.add(IndexDaily(index_date=index_date, base_period_code=base_period, index_level="ROUTE_LEVEL",
                      route_id=routes[0].route_id, window_id=None, index_value=Decimal("100.0000"),
                      price_relative=Decimal("100.0000"), formula_type="JEVONS", routes_included=1))
    db.add(IndexDaily(index_date=index_date, base_period_code=base_period, index_level="ROUTE_LEVEL",
                      route_id=routes[1].route_id, window_id=None, index_value=Decimal("110.0000"),
                      price_relative=Decimal("110.0000"), formula_type="JEVONS", routes_included=1))
    db.commit()

    service = NationalIndexService(db)
    service.compute_national_indices(base_period)

    stmt = select(IndexDaily).where(IndexDaily.index_level == "NATIONAL_COMPOSITE", IndexDaily.index_date == index_date)
    res = db.scalar(stmt)
    assert res.index_value == Decimal("103.7500")

def test_national_index_partial_participation(db):
    """Test 3: One route unavailable. Verify renormalization."""
    routes = setup_basic_routes(db)
    base_period = "TEST-2026"
    index_date = date(2026, 9, 1)

    # Route 1: Index 100, Weight 0.6 (Available)
    # Route 2: No index (Unavailable), Weight 0.4
    # Sum weights = 0.6
    # Normalized: 0.6 / 0.6 = 1.0
    # Expected: 100 * 1.0 = 100.0

    db.add(RouteWeight(route_id=routes[0].route_id, base_period_code=base_period, weight=Decimal("0.6"), passenger_volume=1000, is_active=True))
    db.add(RouteWeight(route_id=routes[1].route_id, base_period_code=base_period, weight=Decimal("0.4"), passenger_volume=1000, is_active=True))

    db.add(IndexDaily(index_date=index_date, base_period_code=base_period, index_level="ROUTE_LEVEL",
                      route_id=routes[0].route_id, window_id=None, index_value=Decimal("100.0000"),
                      price_relative=Decimal("100.0000"), formula_type="JEVONS", routes_included=1))
    db.commit()

    service = NationalIndexService(db)
    service.compute_national_indices(base_period)

    stmt = select(IndexDaily).where(IndexDaily.index_level == "NATIONAL_COMPOSITE", IndexDaily.index_date == index_date)
    res = db.scalar(stmt)
    assert res.index_value == Decimal("100.0000")
    assert res.routes_included == 1

def test_national_index_zero_participation(db):
    """Test 4: No routes have indices for the date. Verify no record is created."""
    routes = setup_basic_routes(db)
    base_period = "TEST-2026"
    index_date = date(2026, 9, 1)

    # Route weights exist, but no ROUTE_LEVEL indices for this date
    db.add(RouteWeight(route_id=routes[0].route_id, base_period_code=base_period, weight=Decimal("1.0"), passenger_volume=1000, is_active=True))
    db.commit()

    service = NationalIndexService(db)
    service.compute_national_indices(base_period)

    stmt = select(IndexDaily).where(IndexDaily.index_level == "NATIONAL_COMPOSITE", IndexDaily.index_date == index_date)
    res = db.scalar(stmt)
    assert res is None

def test_national_index_date_isolation(db):
    """Test 5: Multiple dates. Verify independent calculation."""
    routes = setup_basic_routes(db)
    base_period = "TEST-2026"
    date1 = date(2026, 9, 1)
    date2 = date(2026, 9, 2)

    db.add(RouteWeight(route_id=routes[0].route_id, base_period_code=base_period, weight=Decimal("1.0"), passenger_volume=1000, is_active=True))

    # Date 1: Index 100
    db.add(IndexDaily(index_date=date1, base_period_code=base_period, index_level="ROUTE_LEVEL",
                      route_id=routes[0].route_id, window_id=None, index_value=Decimal("100.0000"),
                      price_relative=Decimal("100.0000"), formula_type="JEVONS", routes_included=1))
    # Date 2: Index 120
    db.add(IndexDaily(index_date=date2, base_period_code=base_period, index_level="ROUTE_LEVEL",
                      route_id=routes[0].route_id, window_id=None, index_value=Decimal("120.0000"),
                      price_relative=Decimal("120.0000"), formula_type="JEVONS", routes_included=1))
    db.commit()

    service = NationalIndexService(db)
    service.compute_national_indices(base_period)

    res1 = db.scalar(select(IndexDaily).where(IndexDaily.index_level == "NATIONAL_COMPOSITE", IndexDaily.index_date == date1))
    res2 = db.scalar(select(IndexDaily).where(IndexDaily.index_level == "NATIONAL_COMPOSITE", IndexDaily.index_date == date2))
    assert res1.index_value == Decimal("100.0000")
    assert res2.index_value == Decimal("120.0000")

def test_national_index_idempotency(db):
    """Test 8: Run twice. Verify no duplicates."""
    routes = setup_basic_routes(db)
    base_period = "TEST-2026"
    index_date = date(2026, 9, 1)

    db.add(RouteWeight(route_id=routes[0].route_id, base_period_code=base_period, weight=Decimal("1.0"), passenger_volume=1000, is_active=True))
    db.add(IndexDaily(index_date=index_date, base_period_code=base_period, index_level="ROUTE_LEVEL",
                      route_id=routes[0].route_id, window_id=None, index_value=Decimal("100.0000"),
                      price_relative=Decimal("100.0000"), formula_type="JEVONS", routes_included=1))
    db.commit()

    service = NationalIndexService(db)
    service.compute_national_indices(base_period)

    # Ensure the first record is committed and the session is cleared
    db.commit()
    db.expire_all()

    service.compute_national_indices(base_period)

    res = db.scalars(select(IndexDaily).where(
        IndexDaily.index_level == "NATIONAL_COMPOSITE",
        IndexDaily.base_period_code == base_period
    )).all()
    assert len(res) == 1
