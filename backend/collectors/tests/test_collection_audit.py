"""Unit and integration tests for CollectionAuditService and persistence monitoring."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Generator
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.base import Base
from backend.app.db.models.airline import Airline
from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.city import City
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.data_source import DataSource
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route import Route
from backend.collectors.collection_audit import (
    BatchAuditSummary,
    CollectionAuditService,
    CollectionHealthStatus,
    PersistenceStatus,
    TaskAuditRecord,
)

# Test SQLite in-memory database with clean schema
@pytest.fixture
def audit_db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Seed base dimensions
    src = DataSource(
        source_id=1,
        source_code="CLEARTRIP",
        source_name="Cleartrip",
        source_type="OTA",
        is_active=True,
    )
    city_del = City(city_id=1, city_code="DEL", city_name="Delhi", state_name="Delhi", is_active=True)
    city_bom = City(city_id=2, city_code="BOM", city_name="Mumbai", state_name="Maharashtra", is_active=True)
    city_blr = City(city_id=3, city_code="BLR", city_name="Bengaluru", state_name="Karnataka", is_active=True)

    route_del_bom = Route(
        route_id=10,
        route_code="DEL-BOM",
        origin_city_id=1,
        destination_city_id=2,
        is_active=True,
    )
    route_blr_del = Route(
        route_id=20,
        route_code="BLR-DEL",
        origin_city_id=3,
        destination_city_id=1,
        is_active=True,
    )

    airline_6e = Airline(
        airline_id=1,
        airline_code="6E",
        airline_name="IndiGo",
        is_active=True,
    )

    window_t1 = BookingWindow(
        window_id=1,
        window_code="T+1",
        target_advance_days=1,
        display_order=1,
        is_active=True,
    )
    window_t7 = BookingWindow(
        window_id=2,
        window_code="T+7",
        target_advance_days=7,
        display_order=2,
        is_active=True,
    )

    session.add_all([
        src,
        city_del,
        city_bom,
        city_blr,
        route_del_bom,
        route_blr_del,
        airline_6e,
        window_t1,
        window_t7,
    ])
    session.commit()

    yield session

    session.close()
    engine.dispose()


def test_audit_healthy_run(audit_db: Session) -> None:
    """A completed run with >= 5 valid quotes is classified as SUCCESS and CONSISTENT."""
    start_time = datetime.now(timezone.utc) - timedelta(seconds=15)
    end_time = datetime.now(timezone.utc)

    run = CollectionRun(
        run_id=101,
        source_id=1,
        target_route_id=10,
        target_window_id=2,
        started_at=start_time,
        completed_at=end_time,
        status="COMPLETED",
        records_scraped=6,
    )
    audit_db.add(run)
    audit_db.flush()

    travel_dt = date(2026, 9, 22)
    obs_time = start_time

    for i in range(6):
        obs = FareObservation(
            observation_id=1000 + i,
            run_id=101,
            data_source_id=1,
            route_id=10,
            airline_id=1,
            window_id=2,
            flight_number=f"6E-{100 + i}",
            observed_at=obs_time,
            travel_date=travel_dt,
            advance_days=7,
            total_fare=Decimal(str(3500 + (i * 100))),
            quality_status="VALID",
            fingerprint_hash=f"hash_{i}_{run.run_id}",
        )
        audit_db.add(obs)
    audit_db.commit()

    service = CollectionAuditService(audit_db)
    record = service.audit_run(101)

    assert record.run_id == 101
    assert record.source_code == "CLEARTRIP"
    assert record.route_code == "DEL-BOM"
    assert record.window_code == "T+7"
    assert record.status == "COMPLETED"
    assert record.health_status == CollectionHealthStatus.SUCCESS
    assert record.persistence_status == PersistenceStatus.CONSISTENT
    assert record.records_scraped == 6
    assert record.db_observation_count == 6
    assert record.distinct_fingerprints == 6
    assert record.valid_count == 6
    assert record.invalid_fare_count == 0
    assert record.min_fare == Decimal("3500.00")
    assert record.max_fare == Decimal("4000.00")
    assert record.duration_seconds is not None
    assert record.duration_seconds >= 14.0
    assert len(record.inconsistency_notes) == 0


def test_audit_no_inventory_run(audit_db: Session) -> None:
    """A completed run with 0 records and confirmed no inventory is classified as NO_INVENTORY and CONSISTENT."""
    start_time = datetime.now(timezone.utc) - timedelta(seconds=5)
    end_time = datetime.now(timezone.utc)

    run = CollectionRun(
        run_id=102,
        source_id=1,
        target_route_id=10,
        target_window_id=1,
        started_at=start_time,
        completed_at=end_time,
        status="COMPLETED",
        records_scraped=0,
        error_summary="Confirmed no inventory: zero flights available on this date.",
    )
    audit_db.add(run)
    audit_db.commit()

    service = CollectionAuditService(audit_db)
    record = service.audit_run(102)

    assert record.run_id == 102
    assert record.status == "COMPLETED"
    assert record.health_status == CollectionHealthStatus.NO_INVENTORY
    assert record.persistence_status == PersistenceStatus.CONSISTENT
    assert record.records_scraped == 0
    assert record.db_observation_count == 0
    assert len(record.inconsistency_notes) == 0


def test_audit_low_data_run(audit_db: Session) -> None:
    """A completed run with 1-4 observations is classified as SUCCESS_WITH_LOW_DATA."""
    start_time = datetime.now(timezone.utc) - timedelta(seconds=8)
    end_time = datetime.now(timezone.utc)

    run = CollectionRun(
        run_id=103,
        source_id=1,
        target_route_id=10,
        target_window_id=1,
        started_at=start_time,
        completed_at=end_time,
        status="COMPLETED",
        records_scraped=3,
    )
    audit_db.add(run)
    audit_db.flush()

    for i in range(3):
        obs = FareObservation(
            observation_id=2000 + i,
            run_id=103,
            data_source_id=1,
            route_id=10,
            airline_id=1,
            window_id=1,
            flight_number=f"6E-{200 + i}",
            observed_at=start_time,
            travel_date=date(2026, 9, 16),
            advance_days=1,
            total_fare=Decimal("4500.00"),
            quality_status="VALID",
            fingerprint_hash=f"hash_low_{i}",
        )
        audit_db.add(obs)
    audit_db.commit()

    service = CollectionAuditService(audit_db, low_data_threshold=5)
    record = service.audit_run(103)

    assert record.health_status == CollectionHealthStatus.SUCCESS_WITH_LOW_DATA
    assert record.persistence_status == PersistenceStatus.CONSISTENT
    assert record.db_observation_count == 3


def test_audit_failed_run(audit_db: Session) -> None:
    """A failed run is classified as FAILED."""
    start_time = datetime.now(timezone.utc) - timedelta(seconds=3)
    end_time = datetime.now(timezone.utc)

    run = CollectionRun(
        run_id=104,
        source_id=1,
        target_route_id=10,
        target_window_id=1,
        started_at=start_time,
        completed_at=end_time,
        status="FAILED",
        records_scraped=0,
        error_summary="Timeout 30000ms exceeded waiting for selector.",
    )
    audit_db.add(run)
    audit_db.commit()

    service = CollectionAuditService(audit_db)
    record = service.audit_run(104)

    assert record.status == "FAILED"
    assert record.health_status == CollectionHealthStatus.FAILED
    assert record.error_summary == "Timeout 30000ms exceeded waiting for selector."


def test_persistence_count_mismatch_detected(audit_db: Session) -> None:
    """When records_scraped != db_observations, persistence is flagged as INCONSISTENT."""
    run = CollectionRun(
        run_id=105,
        source_id=1,
        target_route_id=10,
        target_window_id=1,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        status="COMPLETED",
        records_scraped=10,  # 10 scraped, but only 2 in DB
    )
    audit_db.add(run)
    audit_db.flush()

    for i in range(2):
        obs = FareObservation(
            observation_id=3000 + i,
            run_id=105,
            data_source_id=1,
            route_id=10,
            airline_id=1,
            window_id=1,
            flight_number=f"6E-{300 + i}",
            observed_at=datetime.now(timezone.utc),
            travel_date=date(2026, 9, 16),
            advance_days=1,
            total_fare=Decimal("5000.00"),
            quality_status="VALID",
            fingerprint_hash=f"hash_mismatch_{i}",
        )
        audit_db.add(obs)
    audit_db.commit()

    service = CollectionAuditService(audit_db)
    record = service.audit_run(105)

    assert record.persistence_status == PersistenceStatus.INCONSISTENT
    assert any("Count mismatch: records_scraped=10 but DB fare_observations=2" in note for note in record.inconsistency_notes)


def test_persistence_duplicate_or_missing_fingerprint_detected(audit_db: Session) -> None:
    """When an observation has missing or empty fingerprint hash, persistence is flagged INCONSISTENT."""
    run = CollectionRun(
        run_id=106,
        source_id=1,
        target_route_id=10,
        target_window_id=1,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        status="COMPLETED",
        records_scraped=2,
    )
    audit_db.add(run)
    audit_db.flush()

    obs1 = FareObservation(
        observation_id=4000,
        run_id=106,
        data_source_id=1,
        route_id=10,
        airline_id=1,
        window_id=1,
        flight_number="6E-400",
        observed_at=datetime.now(timezone.utc),
        travel_date=date(2026, 9, 16),
        advance_days=1,
        total_fare=Decimal("4000.00"),
        quality_status="VALID",
        fingerprint_hash="valid_hash_1",
    )
    obs2 = FareObservation(
        observation_id=4001,
        run_id=106,
        data_source_id=1,
        route_id=10,
        airline_id=1,
        window_id=1,
        flight_number="6E-401",
        observed_at=datetime.now(timezone.utc),
        travel_date=date(2026, 9, 16),
        advance_days=1,
        total_fare=Decimal("4000.00"),
        quality_status="VALID",
        fingerprint_hash="",  # Empty hash
    )
    audit_db.add_all([obs1, obs2])
    audit_db.commit()

    service = CollectionAuditService(audit_db)
    record = service.audit_run(106)

    assert record.persistence_status == PersistenceStatus.INCONSISTENT
    assert any("Duplicate fingerprints" in note for note in record.inconsistency_notes)


def test_persistence_route_mismatch_detected(audit_db: Session) -> None:
    """When observation.route_id differs from run.target_route_id, persistence is flagged INCONSISTENT."""
    run = CollectionRun(
        run_id=107,
        source_id=1,
        target_route_id=10,  # Target DEL-BOM (id=10)
        target_window_id=1,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        status="COMPLETED",
        records_scraped=1,
    )
    audit_db.add(run)
    audit_db.flush()

    obs = FareObservation(
        observation_id=5000,
        run_id=107,
        data_source_id=1,
        route_id=20,  # Wrong route: BLR-DEL (id=20)
        airline_id=1,
        window_id=1,
        flight_number="6E-500",
        observed_at=datetime.now(timezone.utc),
        travel_date=date(2026, 9, 16),
        advance_days=1,
        total_fare=Decimal("3000.00"),
        quality_status="VALID",
        fingerprint_hash="hash_route_mismatch",
    )
    audit_db.add(obs)
    audit_db.commit()

    service = CollectionAuditService(audit_db)
    record = service.audit_run(107)

    assert record.persistence_status == PersistenceStatus.INCONSISTENT
    assert any("Route mismatch" in note for note in record.inconsistency_notes)


def test_batch_summary_and_markdown_table(audit_db: Session) -> None:
    """Batch summarization aggregates metrics and renders a clean markdown table."""
    # Seed 2 runs: one success, one failed
    run_a = CollectionRun(
        run_id=201,
        source_id=1,
        target_route_id=10,
        target_window_id=1,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        completed_at=datetime.now(timezone.utc),
        status="COMPLETED",
        records_scraped=5,
    )
    run_b = CollectionRun(
        run_id=202,
        source_id=1,
        target_route_id=20,
        target_window_id=2,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        completed_at=datetime.now(timezone.utc),
        status="FAILED",
        records_scraped=0,
    )
    audit_db.add_all([run_a, run_b])
    audit_db.flush()

    for i in range(5):
        obs = FareObservation(
            observation_id=6000 + i,
            run_id=201,
            data_source_id=1,
            route_id=10,
            airline_id=1,
            window_id=1,
            flight_number=f"6E-{600 + i}",
            observed_at=datetime.now(timezone.utc),
            travel_date=date(2026, 9, 16),
            advance_days=1,
            total_fare=Decimal("3200.00"),
            quality_status="VALID",
            fingerprint_hash=f"hash_batch_{i}",
        )
        audit_db.add(obs)
    audit_db.commit()

    service = CollectionAuditService(audit_db)
    batch = service.audit_batch([201, 202])

    assert batch.total_runs == 2
    assert batch.success_count == 1
    assert batch.failed_count == 1
    assert batch.success_rate == 50.0
    assert batch.failure_rate == 50.0
    assert batch.consistency_rate == 100.0
    assert batch.total_records_scraped == 5
    assert batch.total_db_observations == 5

    table = batch.to_markdown_table()
    assert "Run ID" in table
    assert "201" in table
    assert "DEL-BOM" in table
    assert "CLEARTRIP" in table
    assert "COMPLETED" in table
    assert "202" in table
    assert "FAILED" in table
