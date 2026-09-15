"""
Minimal seed script for testing the APIx index-calculation pipeline
before real scraped/DGCA data is available.

WHAT THIS SEEDS (dependency order):
  cities -> routes -> airlines -> data_sources -> booking_windows
  -> collection_runs -> fare_observations
  -> base_period_fares -> route_weights -> booking_window_weights
  -> dgca_fare_benchmark
"""

from datetime import date, datetime, timedelta
import hashlib
from decimal import Decimal
from sqlalchemy import select, text

from backend.app.db.database import SessionLocal
from backend.app.db.models import (
    City,
    Route,
    Airline,
    DataSource,
    BookingWindow,
    CollectionRun,
    FareObservation,
    BasePeriodFare,
    RouteWeight,
    BookingWindowWeight,
    DGCAFareBenchmark,
)

def get_or_create(session, model, unique_key, key_value, **kwargs):
    """Idempotently retrieve or create a record based on a unique key."""
    stmt = select(model).where(getattr(model, unique_key) == key_value)
    existing = session.scalars(stmt).first()
    if existing:
        for k, v in kwargs.items():
            setattr(existing, k, v)
        session.flush()
        return existing

    new_obj = model(**kwargs, **{unique_key: key_value})
    session.add(new_obj)
    session.flush()
    return new_obj

def seed_data():
    with SessionLocal() as session:
        # ---------------------------------------------------------------------
        # 1. CITIES
        # ---------------------------------------------------------------------
        # We must process these strictly one-by-one and avoid any lists of objects
        # to prevent SQLAlchemy from batching them into a single INSERT.

        delhi = get_or_create(session, City, "city_code", "DEL",
                               city_name="Delhi", state_name="Delhi", is_metro=True, is_active=True)
        session.flush()

        mumbai = get_or_create(session, City, "city_code", "BOM",
                                city_name="Mumbai", state_name="Maharashtra", is_metro=True, is_active=True)
        session.flush()

        bengaluru = get_or_create(session, City, "city_code", "BLR",
                                   city_name="Bengaluru", state_name="Karnataka", is_metro=True, is_active=True)
        session.flush()

        # ---------------------------------------------------------------------
        # 2. ROUTES
        # ---------------------------------------------------------------------
        route_del_bom = get_or_create(session, Route, "route_code", "DEL-BOM",
            origin_city_id=delhi.city_id,
            destination_city_id=mumbai.city_id,
            is_active=True
        )
        session.flush()

        route_blr_del = get_or_create(session, Route, "route_code", "BLR-DEL",
            origin_city_id=bengaluru.city_id,
            destination_city_id=delhi.city_id,
            is_active=True
        )
        session.flush()

        # ---------------------------------------------------------------------
        # 3. AIRLINES
        # ---------------------------------------------------------------------
        indigo = get_or_create(session, Airline, "airline_code", "6E",
                               airline_name="IndiGo", is_active=True)
        session.flush()

        # ---------------------------------------------------------------------
        # 4. DATA SOURCES
        # ---------------------------------------------------------------------
        makemytrip = get_or_create(session, DataSource, "source_code", "MMT",
                                    source_name="MakeMyTrip", source_type="OTA", is_active=True)
        session.flush()

        # ---------------------------------------------------------------------
        # 5. BOOKING WINDOWS
        # ---------------------------------------------------------------------
        window_t1 = get_or_create(session, BookingWindow, "window_code", "T+1",
                                   target_advance_days=1, display_order=1, is_active=True)
        session.flush()

        window_t7 = get_or_create(session, BookingWindow, "window_code", "T+7",
                                   target_advance_days=7, display_order=2, is_active=True)
        session.flush()

        # ---------------------------------------------------------------------
        # 6. COLLECTION RUN
        # ---------------------------------------------------------------------
        session.execute(text("DELETE FROM collection_runs"))
        session.execute(text("DELETE FROM fare_observations"))

        run = CollectionRun(
            source_id=makemytrip.source_id,
            target_route_id=route_del_bom.route_id,
            target_window_id=window_t7.window_id,
            started_at=datetime(2026, 9, 6, 10, 0, 0),
            status="SUCCESS",
            records_scraped=15,
        )
        session.add(run)
        session.flush()

        # ---------------------------------------------------------------------
        # 7. FARE OBSERVATIONS
        # ---------------------------------------------------------------------
        def make_fingerprint(*parts):
            return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()

        def make_observation(route, window, fare, advance_days, quality_status="VALID", is_available=True, flight_no=None):
            travel_date = date(2026, 9, 6) + timedelta(days=advance_days)
            # Generate a unique flight number if none provided to avoid over-reconciliation
            if flight_no is None:
                flight_no = f"6E{hashlib.md5(str(fare).encode()).hexdigest()[:4].upper()}"
            return FareObservation(
                run_id=run.run_id,
                data_source_id=makemytrip.source_id,
                route_id=route.route_id,
                airline_id=indigo.airline_id,
                window_id=window.window_id,
                flight_number=flight_no,
                observed_at=datetime(2026, 9, 6, 10, 0, 0),
                travel_date=travel_date,
                advance_days=advance_days,
                origin_airport_code=route.route_code.split("-")[0],
                destination_airport_code=route.route_code.split("-")[1],
                cabin_class="ECONOMY",
                is_non_stop=True,
                stops=0,
                total_fare=Decimal(str(fare)),
                base_fare=None,
                fuel_surcharge=None,
                taxes_and_fees=None,
                convenience_fee=None,
                currency="INR",
                is_available=is_available,
                seats_remaining=None,
                quality_status=quality_status,
                fingerprint_hash=make_fingerprint(route.route_id, window.window_id, fare, flight_no, advance_days),
            )

        observations = []
        for fare in [7200, 7350, 7500]:
            observations.append(make_observation(route_del_bom, window_t1, fare, advance_days=1))
        for fare in [7500, 7800, 7900, 8100, 8500]:
            observations.append(make_observation(route_del_bom, window_t7, fare, advance_days=7))
        for fare in [6800, 7000]:
            observations.append(make_observation(route_blr_del, window_t1, fare, advance_days=1))
        for fare in [7200, 7400, 7600]:
            observations.append(make_observation(route_blr_del, window_t7, fare, advance_days=7))
        observations.append(make_observation(route_del_bom, window_t7, 1.00, advance_days=7, quality_status="INVALID_FARE"))
        observations.append(make_observation(route_del_bom, window_t7, 9200, advance_days=7, is_available=False))

        session.add_all(observations)
        session.flush()

        # ---------------------------------------------------------------------
        # 8. BASE PERIOD FARES
        # ---------------------------------------------------------------------
        session.execute(text("DELETE FROM base_period_fares WHERE base_period_code = 'JUL-2026'"))

        base_fares = [
            BasePeriodFare(base_period_code="JUL-2026", route_id=route_del_bom.route_id, window_id=window_t1.window_id, benchmark_fare=Decimal("5800"), sample_size=10),
            BasePeriodFare(base_period_code="JUL-2026", route_id=route_del_bom.route_id, window_id=window_t7.window_id, benchmark_fare=Decimal("6000"), sample_size=10),
            BasePeriodFare(base_period_code="JUL-2026", route_id=route_blr_del.route_id, window_id=window_t1.window_id, benchmark_fare=Decimal("6200"), sample_size=10),
            BasePeriodFare(base_period_code="JUL-2026", route_id=route_blr_del.route_id, window_id=window_t7.window_id, benchmark_fare=Decimal("6400"), sample_size=10),
        ]
        session.add_all(base_fares)

        # ---------------------------------------------------------------------
        # 9. ROUTE WEIGHTS
        # ---------------------------------------------------------------------
        session.execute(text("DELETE FROM route_weights WHERE base_period_code = 'JUL-2026'"))

        weights = [
            RouteWeight(route_id=route_del_bom.route_id, base_period_code="JUL-2026", passenger_volume=459060, weight=Decimal("0.60"), is_active=True),
            RouteWeight(route_id=route_blr_del.route_id, base_period_code="JUL-2026", passenger_volume=306040, weight=Decimal("0.40"), is_active=True),
        ]
        session.add_all(weights)

        # ---------------------------------------------------------------------
        # 10. BOOKING WINDOW WEIGHTS
        # ---------------------------------------------------------------------
        session.execute(text("DELETE FROM booking_window_weights WHERE is_active = true"))

        window_weights = [
            BookingWindowWeight(window_id=window_t1.window_id, weight=Decimal("0.5000"), source_note="placeholder", is_active=True),
            BookingWindowWeight(window_id=window_t7.window_id, weight=Decimal("0.5000"), source_note="placeholder", is_active=True),
        ]
        session.add_all(window_weights)

        # ---------------------------------------------------------------------
        # 11. DGCA FARE BENCHMARK
        # ---------------------------------------------------------------------
        session.execute(text("DELETE FROM dgca_fare_benchmark WHERE route_id IS NULL AND period_start = '2026-09-01'"))

        dgca_benchmark = DGCAFareBenchmark(
            route_id=None,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            dgca_avg_fare=Decimal("6850.00"),
            report_source="DGCA monthly fare report, Sep 2026",
        )
        session.add(dgca_benchmark)

        session.commit()
        print("Seed complete.")
        print(f"  Routes: DEL-BOM (id={route_del_bom.route_id}), BLR-DEL (id={route_blr_del.route_id})")
        print(f"  Booking windows: T+1 (id={window_t1.window_id}), T+7 (id={window_t7.window_id})")

if __name__ == "__main__":
    seed_data()
