import pytest
import logging
from datetime import date, datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select

from backend.app.db.database import SessionLocal
from backend.collectors.playwright.yatra_collector import YatraCollector
from backend.processing.ingestion.repository import IngestionRepository
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.collectors.playwright.schemas.flight_quote import FlightQuote

# Configure logging to see scraper output in pytest
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@pytest.fixture
def db_session():
    """Provide a clean DB session for each test."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def collector():
    """Provide a fresh Yatra collector."""
    return YatraCollector()

def test_stage1_dry_run(collector):
    """
    STAGE 1: DRY RUN
    Verify the collector fetches real data without persisting to DB.
    """
    origin = "DEL"
    destination = "BOM"
    # Use a date in the future (T+7)
    travel_date = date.today() + timedelta(days=7)

    logger.info(f"Running Stage 1: Dry Run for {origin} -> {destination} on {travel_date}")
    quotes = collector.collect(origin=origin, destination=destination, travel_date=travel_date)

    logger.info(f"Fetched {len(quotes)} quotes.")
    for i, q in enumerate(quotes):
        logger.info(f"Quote {i}: Flight={q.flight_number}, Fare={q.total_fare}, Date={q.travel_date}")

    assert len(quotes) > 0, "No quotes fetched from Air India Express"
    for q in quotes:
        assert q.flight_number is not None
        assert q.total_fare > 0
        assert q.travel_date == travel_date

def test_stage2_db_write(db_session, collector):
    """
    STAGE 2: DB WRITE TEST
    Verify the full ingestion flow from quotes to DB.
    """
    origin = "DEL"
    destination = "BOM"
    travel_date = date.today() + timedelta(days=7)

    logger.info("Running Stage 2: DB Write Test")
    quotes = collector.collect(origin=origin, destination=destination, travel_date=travel_date)
    assert len(quotes) > 0

    repo = IngestionRepository(db_session)
    result = repo.ingest_quotes(quotes)

    logger.info(f"Ingestion result: {result}")

    # Verify collection_run
    run = db_session.get(CollectionRun, result.run_id)
    assert run is not None
    assert run.status == "COMPLETED"
    assert run.records_scraped == result.inserted

    # Verify fare_observations
    stmt = select(FareObservation).where(FareObservation.run_id == result.run_id)
    observations = db_session.scalars(stmt).all()

    assert len(observations) == result.inserted
    for obs in observations:
        assert obs.total_fare > 0
        assert obs.fingerprint_hash is not None
        assert obs.run_id == result.run_id

    logger.info(f"Verified {len(observations)} observations in DB for run {result.run_id}")

def test_stage3_failure_handling(db_session, collector, monkeypatch):
    """
    STAGE 3: FAILURE HANDLING TEST
    Simulate a failure and verify run status and transaction rollback.
    """
    # We can simulate failure by mocking the collector's collect method
    # or simply using a route that doesn't exist/fails.
    # Better: mock an exception during ingestion to test DB rollback.

    from backend.processing.ingestion.resolver import DimensionResolver

    def mock_resolve_dimensions(*args, **kwargs):
        raise RuntimeError("Simulated Dimension Resolution Failure")

    # Monkeypatch DimensionResolver.resolve_dimensions
    monkeypatch.setattr(DimensionResolver, "resolve_dimensions", mock_resolve_dimensions)

    repo = IngestionRepository(db_session)

    # Create some dummy quotes
    dummy_quote = FlightQuote(
        airline="Air India Express",
        flight_number="IX 123",
        origin="DEL",
        destination="BOM",
        travel_date=date.today() + timedelta(days=7),
        departure_time=None,
        arrival_time=None,
        cabin_class="ECONOMY",
        fare_family="Xpress Value",
        stops=0,
        total_fare=5000,
        currency="INR",
        availability=True,
        baggage=None,
        source="Air India Express Direct",
        observed_at=datetime.now(timezone.utc)
    )

    logger.info("Running Stage 3: Failure Handling Test")

    # Ingest quotes that will fail mapping/resolution
    try:
        repo.ingest_quotes([dummy_quote])
    except Exception as e:
        logger.info(f"Caught expected exception: {e}")

    # The IngestionRepository.ingest_quotes handles internal mapping errors
    # but not total transactional failures if it catches them.
    # Let's check if a run was created and marked FAILED.

    # Since ingest_quotes creates a run first, let's find the last run.
    stmt = select(CollectionRun).order_by(CollectionRun.run_id.desc()).limit(1)
    last_run = db_session.scalar(stmt)

    assert last_run is not None
    assert last_run.status == "FAILED"

    # Verify no observations were committed for this failed run
    obs_stmt = select(FareObservation).where(FareObservation.run_id == last_run.run_id)
    observations = db_session.scalars(obs_stmt).all()
    assert len(observations) == 0
    logger.info("Verified no observations were committed for failed run.")

def test_stage4_idempotency(db_session, collector):
    """
    STAGE 4: IDEMPOTENCY TEST
    Verify fingerprint_hash prevents duplicates within a run.
    """
    origin = "DEL"
    destination = "BOM"
    travel_date = date.today() + timedelta(days=7)

    logger.info("Running Stage 4: Idempotency Test")
    quotes = collector.collect(origin=origin, destination=destination, travel_date=travel_date)
    assert len(quotes) > 0

    repo = IngestionRepository(db_session)

    # Run 1: Normal ingestion
    result1 = repo.ingest_quotes(quotes)
    inserted1 = result1.inserted

    # Run 2: Same quotes, same run_id (to test intra-run deduplication)
    # In a real scenario, we'd pass the same run object.
    run = db_session.get(CollectionRun, result1.run_id)
    result2 = repo.ingest_quotes(quotes, run=run)

    assert result2.skipped == inserted1
    assert result2.inserted == 0

    # Verify total observations for this run is still the same
    obs_stmt = select(FareObservation).where(FareObservation.run_id == run.run_id)
    total_obs = len(db_session.scalars(obs_stmt).all())
    assert total_obs == inserted1

    logger.info(f"Idempotency verified: {inserted1} inserted, {result2.skipped} skipped.")
