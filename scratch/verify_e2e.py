import logging
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from backend.app.db.base import Base
from backend.collectors.playwright.yatra_collector import YatraCollector
from backend.processing.ingestion.repository import IngestionRepository
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.processing.ingestion.resolver import DimensionResolver

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Use SQLite for testing to avoid connectivity issues with Neon
TEST_DB_URL = "sqlite:///:memory:"
engine = create_engine(TEST_DB_URL)
SessionLocal = sessionmaker(bind=engine)

def setup_db():
    Base.metadata.create_all(bind=engine)

def run_tests():
    setup_db()
    db = SessionLocal()
    collector = YatraCollector()
    repo = IngestionRepository(db)

    # Stage 1: Dry Run
    logger.info("--- Stage 1: Dry Run ---")
    origin, destination = "DEL", "BOM"
    travel_date = date.today() + timedelta(days=7)
    quotes = collector.collect(origin=origin, destination=destination, travel_date=travel_date)
    logger.info(f"Fetched {len(quotes)} quotes.")
    for q in quotes[:2]:
        logger.info(f"Sample Quote: {q}")

    if not quotes:
        logger.error("Stage 1 FAILED: No quotes fetched.")
        return

    # Stage 2: DB Write
    logger.info("\n--- Stage 2: DB Write ---")
    # Need to seed some basic reference data because we are in a fresh SQLite DB
    # In a real scenario, these would exist in the DB.
    from backend.app.db.models.airline import Airline
    from backend.app.db.models.city import City
    from backend.app.db.models.route import Route
    from backend.app.db.models.booking_window import BookingWindow
    from backend.app.db.models.data_source import DataSource

    # Seed minimal data
    city_del = City(city_id=1, city_code="DEL", city_name="Delhi", state_name="Delhi", is_metro=True, is_active=True)
    city_bom = City(city_id=2, city_code="BOM", city_name="Mumbai", state_name="Maharashtra", is_metro=True, is_active=True)
    db.add_all([city_del, city_bom])
    db.commit()

    route = Route(route_id=1, origin_city_id=city_del.city_id, destination_city_id=city_bom.city_id, route_code="DEL-BOM", is_active=True)
    db.add(route)
    db.commit()

    window = BookingWindow(window_id=1, window_code="W7", target_advance_days=7, display_order=1, is_active=True)
    db.add(window)
    db.commit()

    source = DataSource(source_id=1, source_code="YATRA", source_name="Yatra", source_type="OTA", is_active=True)
    db.add(source)
    db.commit()

    airline = Airline(airline_id=1, airline_code="AI", airline_name="Air India", is_active=True)
    db.add(airline)
    db.commit()

    result = repo.ingest_quotes(quotes)
    logger.info(f"Ingestion Result: {result}")

    run = db.get(CollectionRun, result.run_id)
    logger.info(f"Collection Run: ID={run.run_id}, Status={run.status}, Records={run.records_scraped}")

    obs_stmt = select(FareObservation).where(FareObservation.run_id == run.run_id)
    observations = db.scalars(obs_stmt).all()
    logger.info(f"Inserted {len(observations)} observations.")
    for obs in observations[:2]:
        logger.info(f"Observation: Fare={obs.total_fare}, Hash={obs.fingerprint_hash[:12]}...")

    # Stage 3: Failure Handling
    logger.info("\n--- Stage 3: Failure Handling ---")
    # Simulate a failure by using a corrupted quote that fails mapping
    corrupted_quote = FlightQuote(
        airline="Invalid",
        flight_number=None, # Should cause a failure in resolution/mapping
        origin="DEL",
        destination="BOM",
        travel_date=travel_date,
        departure_time=None,
        arrival_time=None,
        cabin_class="ECONOMY",
        fare_family=None,
        stops=0,
        total_fare=-100, # Should fail validation
        currency="INR",
        availability=True,
        baggage=None,
        source="Yatra",
        observed_at=datetime.now(timezone.utc)
    )

    try:
        # This might not raise an exception if ingest_quotes catches it internally
        repo.ingest_quotes([corrupted_quote])
    except Exception as e:
        logger.info(f"Caught expected exception: {e}")

    # Check for failed run
    stmt = select(CollectionRun).order_by(CollectionRun.run_id.desc()).limit(1)
    last_run = db.scalar(stmt)
    logger.info(f"Last run status: {last_run.status if last_run else 'None'}")

    # Stage 4: Idempotency
    logger.info("\n--- Stage 4: Idempotency ---")
    # Ingest the same quotes again into the same run
    result_dup = repo.ingest_quotes(quotes, run=run)
    logger.info(f"Duplicate Ingestion Result: Inserted={result_dup.inserted}, Skipped={result_dup.skipped}")

    obs_stmt = select(FareObservation).where(FareObservation.run_id == run.run_id)
    total_obs = len(db.scalars(obs_stmt).all())
    logger.info(f"Total observations for run {run.run_id}: {total_obs}")

    if result_dup.inserted == 0 and result_dup.skipped == len(quotes):
        logger.info("Idempotency Verified!")
    else:
        logger.error("Idempotency Failed!")

if __name__ == "__main__":
    run_tests()
