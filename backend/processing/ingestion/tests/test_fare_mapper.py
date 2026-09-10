"""Unit and database integration tests for fare mapping and ingestion repository."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select

from backend.app.db.database import engine, SessionLocal
from backend.app.db.models.airline import Airline
from backend.app.db.models.data_source import DataSource
from backend.app.db.models.route import Route
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.seed import seed_booking_windows
from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.processing.ingestion.resolver import DimensionResolver, ResolvedDimensions
from backend.processing.ingestion.fare_mapper import map_quote_to_observation
from backend.processing.ingestion.repository import IngestionRepository


@pytest.fixture(scope="function")
def db():
    """Provide a transactional test database session with rollback."""
    conn = engine.connect()
    tx = conn.begin()
    session = SessionLocal(bind=conn)

    # Ensure booking windows are seeded
    seed_booking_windows(session)

    yield session

    session.close()
    if tx.is_active:
        tx.rollback()
    conn.close()


def make_quote(
    flight_number: str = "SG 162",
    total_fare: Decimal = Decimal("7890.00"),
    departure_time: time = time(19, 55),
    arrival_time: time = time(22, 40),
    travel_date: date = date(2026, 9, 15),
    observed_at: datetime = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
) -> FlightQuote:
    """Build a standard FlightQuote for tests."""
    return FlightQuote(
        airline="SpiceJet",
        flight_number=flight_number,
        origin="DEL",
        destination="BOM",
        travel_date=travel_date,
        departure_time=departure_time,
        arrival_time=arrival_time,
        cabin_class="ECONOMY",
        fare_family="SpiceSaver",
        stops=0,
        total_fare=total_fare,
        currency="INR",
        availability=True,
        source="SpiceJet Direct",
        observed_at=observed_at,
    )


def test_map_quote_to_observation_unit():
    """Unit test verifying that map_quote_to_observation maps all fields accurately."""
    quote = make_quote()
    dims = ResolvedDimensions(
        airline_id=1,
        data_source_id=2,
        route_id=3,
        window_id=4,
        advance_days=7,
    )
    obs = map_quote_to_observation(quote, run_id=10, dimensions=dims)

    assert obs.run_id == 10
    assert obs.airline_id == 1
    assert obs.data_source_id == 2
    assert obs.route_id == 3
    assert obs.window_id == 4
    assert obs.flight_number == "SG 162"
    assert obs.advance_days == 7
    assert obs.travel_date == date(2026, 9, 15)
    assert obs.scheduled_departure_time == time(19, 55)
    assert obs.scheduled_arrival_time == time(22, 40)
    assert obs.origin_airport_code == "DEL"
    assert obs.destination_airport_code == "BOM"
    assert obs.cabin_class == "ECONOMY"
    assert obs.fare_family == "SpiceSaver"
    assert obs.is_non_stop is True
    assert obs.stops == 0
    assert obs.total_fare == Decimal("7890.00")
    assert obs.currency == "INR"
    assert obs.is_available is True
    assert obs.quality_status == "VALID"
    assert len(obs.fingerprint_hash) == 64
    # Nullable scraper fields remain None
    assert obs.base_fare is None
    assert obs.fuel_surcharge is None
    assert obs.taxes_and_fees is None
    assert obs.convenience_fee is None
    assert obs.seats_remaining is None


def test_resolver_dimensions(db):
    """Test resolution of Airline, DataSource, Route, and BookingWindow without hardcoded IDs."""
    resolver = DimensionResolver(db, auto_create_reference_data=True)
    quote = make_quote()

    dims = resolver.resolve_dimensions(quote)

    assert dims.airline_id > 0
    assert dims.data_source_id > 0
    assert dims.route_id > 0
    assert dims.window_id > 0
    assert dims.advance_days == 7

    # Verify entities exist in database
    airline = db.get(Airline, dims.airline_id)
    assert airline.airline_code == "SG"
    assert "SpiceJet" in airline.airline_name

    source = db.get(DataSource, dims.data_source_id)
    assert "SPICEJET" in source.source_code

    route = db.get(Route, dims.route_id)
    assert route.route_code == "DEL-BOM"


def test_ingestion_repository_intra_run_duplicate_handling(db):
    """Test that identical quotes within the same run are skipped gracefully without error."""
    repo = IngestionRepository(db)
    q1 = make_quote(flight_number="SG 162", total_fare=Decimal("7890.00"))
    q2 = make_quote(flight_number="SG 162", total_fare=Decimal("7890.00"))  # Duplicate

    result = repo.ingest_quotes([q1, q2])

    assert result.total_quotes == 2
    assert result.inserted == 1
    assert result.skipped == 1
    assert result.failed == 0


def test_same_flight_in_different_runs_allowed(db):
    """Test that the same flight product in different CollectionRuns is allowed and persisted."""
    repo = IngestionRepository(db)

    # First run
    q1 = make_quote(flight_number="SG 162", total_fare=Decimal("7890.00"))
    res1 = repo.ingest_quotes([q1])
    assert res1.inserted == 1

    # Second run (e.g. later time or updated fare)
    q2 = make_quote(flight_number="SG 162", total_fare=Decimal("8100.00"))
    res2 = repo.ingest_quotes([q2])
    assert res2.inserted == 1
    assert res2.run_id != res1.run_id


def test_ingest_spicejet_poc_quotes(db):
    """Integration test: Ingest the real SpiceJet quotes extracted during Step 2 POC."""
    repo = IngestionRepository(db)

    quotes = [
        FlightQuote(
            airline="SpiceJet",
            flight_number="SG 162",
            origin="DEL",
            destination="BOM",
            travel_date=date(2026, 9, 15),
            departure_time=time(19, 55),
            arrival_time=time(22, 40),
            cabin_class="ECONOMY",
            fare_family="SpiceSaver",
            stops=0,
            total_fare=Decimal("7890.00"),
            currency="INR",
            availability=True,
            source="SpiceJet Direct",
            observed_at=datetime(2026, 9, 8, 17, 51, 18, tzinfo=timezone.utc),
        ),
        FlightQuote(
            airline="SpiceJet",
            flight_number="SG 2802",
            origin="DEL",
            destination="BOM",
            travel_date=date(2026, 9, 15),
            departure_time=time(22, 45),
            arrival_time=time(1, 25),
            cabin_class="ECONOMY",
            fare_family="SpiceSaver",
            stops=0,
            total_fare=Decimal("20769.00"),
            currency="INR",
            availability=True,
            source="SpiceJet Direct",
            observed_at=datetime(2026, 9, 8, 17, 51, 18, tzinfo=timezone.utc),
        ),
    ]

    result = repo.ingest_quotes(quotes)

    assert result.total_quotes == 2
    assert result.inserted == 2
    assert result.skipped == 0
    assert result.failed == 0

    # Query back from database to verify persistence
    run = db.get(CollectionRun, result.run_id)
    assert run is not None
    assert run.status == "COMPLETED"
    assert run.records_scraped == 2

    stmt = select(FareObservation).where(FareObservation.run_id == result.run_id).order_by(FareObservation.flight_number)
    observations = db.scalars(stmt).all()
    assert len(observations) == 2

    # Verify first flight (SG 162)
    obs1 = observations[0]
    assert obs1.flight_number == "SG 162"
    assert obs1.total_fare == Decimal("7890.00")
    assert obs1.scheduled_departure_time == time(19, 55)
    assert obs1.scheduled_arrival_time == time(22, 40)
    assert obs1.is_non_stop is True
    assert obs1.quality_status == "VALID"
    assert len(obs1.fingerprint_hash) == 64

    # Verify second flight (SG 2802)
    obs2 = observations[1]
    assert obs2.flight_number == "SG 2802"
    assert obs2.total_fare == Decimal("20769.00")
    assert obs2.scheduled_departure_time == time(22, 45)
    assert obs2.scheduled_arrival_time == time(1, 25)
    assert obs2.is_non_stop is True
    assert obs2.quality_status == "VALID"
    assert obs1.fingerprint_hash != obs2.fingerprint_hash
