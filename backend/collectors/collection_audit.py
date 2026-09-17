"""Collection Monitoring and Persistence Audit Layer.

Provides programmatic operational monitoring, health classification, and end-to-end
persistence consistency audits directly against existing PostgreSQL tables (CollectionRun
and FareObservation) without modifying database schema.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.data_source import DataSource
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route import Route
from backend.processing.cleaning.outlier_detection import OutlierDetector

logger = logging.getLogger(__name__)

PROJECT_TIMEZONE = ZoneInfo(os.getenv("PROJECT_TIMEZONE", "Asia/Kolkata"))

# Established minimum sample guardrail from project's OutlierDetector
LOW_DATA_THRESHOLD = getattr(OutlierDetector, "min_sample_size", 5)
if isinstance(LOW_DATA_THRESHOLD, property) or not isinstance(LOW_DATA_THRESHOLD, int):
    LOW_DATA_THRESHOLD = 5


class CollectionHealthStatus(str, Enum):
    """Operational health classification for a single collection run."""

    SUCCESS = "SUCCESS"
    SUCCESS_WITH_LOW_DATA = "SUCCESS_WITH_LOW_DATA"
    NO_INVENTORY = "NO_INVENTORY"
    FAILED = "FAILED"


class PersistenceStatus(str, Enum):
    """Data integrity status verifying database persistence consistency."""

    CONSISTENT = "CONSISTENT"
    INCONSISTENT = "INCONSISTENT"


@dataclass
class TaskAuditRecord:
    """Comprehensive operational audit for a single CollectionRun."""

    run_id: int
    source_code: str
    route_code: str
    window_code: str
    target_advance_days: int
    observation_date: date
    travel_date: Optional[date]
    status: str  # 'COMPLETED', 'FAILED', 'RUNNING'
    health_status: CollectionHealthStatus
    persistence_status: PersistenceStatus
    records_scraped: int
    db_observation_count: int
    distinct_fingerprints: int
    valid_count: int
    invalid_fare_count: int
    min_fare: Optional[Decimal] = None
    max_fare: Optional[Decimal] = None
    avg_fare: Optional[Decimal] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error_summary: Optional[str] = None
    inconsistency_notes: List[str] = field(default_factory=list)

    @property
    def is_consistent(self) -> bool:
        return self.persistence_status == PersistenceStatus.CONSISTENT


@dataclass
class BatchAuditSummary:
    """Aggregated operational and persistence audit across multiple collection runs."""

    total_runs: int = 0
    success_count: int = 0
    low_data_count: int = 0
    no_inventory_count: int = 0
    failed_count: int = 0
    consistent_count: int = 0
    inconsistent_count: int = 0
    total_records_scraped: int = 0
    total_db_observations: int = 0
    average_duration_seconds: float = 0.0
    records: List[TaskAuditRecord] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """Percentage of runs that completed successfully (including low data)."""
        if self.total_runs == 0:
            return 0.0
        return ((self.success_count + self.low_data_count) / self.total_runs) * 100.0

    @property
    def no_inventory_rate(self) -> float:
        """Percentage of runs that completed with confirmed zero inventory."""
        if self.total_runs == 0:
            return 0.0
        return (self.no_inventory_count / self.total_runs) * 100.0

    @property
    def failure_rate(self) -> float:
        """Percentage of runs that failed technically or had unexpected empty parser results."""
        if self.total_runs == 0:
            return 0.0
        return (self.failed_count / self.total_runs) * 100.0

    @property
    def consistency_rate(self) -> float:
        """Percentage of runs with 100% verified persistence consistency."""
        if self.total_runs == 0:
            return 0.0
        return (self.consistent_count / self.total_runs) * 100.0

    def to_markdown_table(self) -> str:
        """Format batch audit records into a GitHub-flavored markdown table."""
        headers = [
            "Run ID",
            "Route",
            "Window",
            "Source",
            "Status",
            "Health",
            "Scraped",
            "DB Obs",
            "Fingerprints",
            "Travel Date",
            "Duration (s)",
            "Persistence",
        ]
        sep = ["---"] * len(headers)
        rows = [" | ".join(headers), " | ".join(sep)]

        for r in self.records:
            dur_str = f"{r.duration_seconds:.1f}" if r.duration_seconds is not None else "N/A"
            travel_str = str(r.travel_date) if r.travel_date else "N/A"
            row = [
                str(r.run_id),
                r.route_code,
                r.window_code,
                r.source_code,
                r.status,
                r.health_status.value,
                str(r.records_scraped),
                str(r.db_observation_count),
                str(r.distinct_fingerprints),
                travel_str,
                dur_str,
                r.persistence_status.value,
            ]
            rows.append(" | ".join(row))

        return "\n".join(rows)


class CollectionAuditService:
    """Service providing programmatic collection monitoring and database persistence auditing."""

    def __init__(
        self,
        db: Session,
        timezone_override: Optional[Union[ZoneInfo, timezone]] = None,
        low_data_threshold: int = LOW_DATA_THRESHOLD,
    ) -> None:
        """Initialize audit service with a database session.

        Args:
            db: Active SQLAlchemy database session.
            timezone_override: Timezone used for observation date derivation (default: Asia/Kolkata).
            low_data_threshold: Minimum observation count below which runs are tagged SUCCESS_WITH_LOW_DATA.
        """
        self.db = db
        self.timezone = timezone_override or PROJECT_TIMEZONE
        self.low_data_threshold = low_data_threshold

    def audit_run(self, run_id: int) -> TaskAuditRecord:
        """Audit a single CollectionRun and all associated FareObservations in PostgreSQL.

        Queries existing database tables to evaluate operational health and persistence
        invariants across 9 criteria without relying on in-memory collector state.

        Args:
            run_id: ID of the CollectionRun to audit.

        Returns:
            TaskAuditRecord populated with database metrics and integrity evaluation.

        Raises:
            ValueError: If the specified run_id does not exist in collection_runs.
        """
        stmt = (
            select(CollectionRun)
            .options(
                joinedload(CollectionRun.data_source),
                joinedload(CollectionRun.target_route),
                joinedload(CollectionRun.booking_window),
            )
            .where(CollectionRun.run_id == run_id)
        )
        run = self.db.scalars(stmt).first()
        if not run:
            raise ValueError(f"CollectionRun with run_id={run_id} not found.")

        # Extract related entity metadata
        source_code = run.data_source.source_code if run.data_source else "UNKNOWN"
        route_code = run.target_route.route_code if run.target_route else "UNKNOWN"
        window_code = run.booking_window.window_code if run.booking_window else "UNKNOWN"
        target_advance_days = (
            run.booking_window.target_advance_days if run.booking_window else 0
        )

        # Derive observation date in project timezone
        if run.started_at:
            started_local = run.started_at.astimezone(self.timezone)
            observation_date = started_local.date()
        else:
            observation_date = datetime.now(self.timezone).date()

        # Compute duration
        duration_seconds: Optional[float] = None
        if run.started_at and run.completed_at:
            duration_seconds = round(
                (run.completed_at - run.started_at).total_seconds(), 2
            )

        # Query all fare observations linked to this run_id
        obs_stmt = select(FareObservation).where(FareObservation.run_id == run_id)
        observations = self.db.scalars(obs_stmt).all()

        db_observation_count = len(observations)
        distinct_fingerprints = len({obs.fingerprint_hash for obs in observations if obs.fingerprint_hash})
        valid_count = sum(1 for obs in observations if obs.quality_status == "VALID")
        invalid_fare_count = sum(
            1 for obs in observations if obs.total_fare is None or obs.total_fare <= 0
        )

        # Fare metrics
        fares = [obs.total_fare for obs in observations if obs.total_fare is not None and obs.total_fare > 0]
        min_fare = min(fares) if fares else None
        max_fare = max(fares) if fares else None
        avg_fare = round(sum(fares) / Decimal(len(fares)), 2) if fares else None

        # Derive target travel date
        travel_dates = {obs.travel_date for obs in observations if obs.travel_date}
        travel_date = next(iter(travel_dates)) if travel_dates else (observation_date + timedelta(days=target_advance_days))

        # Check persistence invariants & consistency
        inconsistency_notes: List[str] = []

        # Check 1: records_scraped vs fare_observations count
        if run.status == "COMPLETED":
            if run.records_scraped != db_observation_count:
                inconsistency_notes.append(
                    f"Count mismatch: records_scraped={run.records_scraped} but DB fare_observations={db_observation_count}."
                )

        # Check 2: intra-run fingerprint uniqueness
        if distinct_fingerprints != db_observation_count:
            inconsistency_notes.append(
                f"Duplicate fingerprints: {db_observation_count - distinct_fingerprints} duplicate hashes detected within run."
            )

        # Check 3: zero or invalid fares
        if invalid_fare_count > 0:
            inconsistency_notes.append(
                f"Invalid fares: {invalid_fare_count} observations have total_fare <= 0 or NULL."
            )

        # Check 4: route_id consistency
        if run.target_route_id is not None:
            mismatched_routes = [
                obs.route_id for obs in observations if obs.route_id != run.target_route_id
            ]
            if mismatched_routes:
                inconsistency_notes.append(
                    f"Route mismatch: {len(mismatched_routes)} observations have route_id != {run.target_route_id}."
                )

        # Check 5: booking_window_id consistency
        if run.target_window_id is not None:
            mismatched_windows = [
                obs.window_id for obs in observations if obs.window_id != run.target_window_id
            ]
            if mismatched_windows:
                inconsistency_notes.append(
                    f"Window mismatch: {len(mismatched_windows)} observations have window_id != {run.target_window_id}."
                )

        # Check 6: data_source_id consistency
        if run.source_id is not None:
            mismatched_sources = [
                obs.data_source_id for obs in observations if obs.data_source_id != run.source_id
            ]
            if mismatched_sources:
                inconsistency_notes.append(
                    f"Data source mismatch: {len(mismatched_sources)} observations have data_source_id != {run.source_id}."
                )

        # Check 7: travel date consistency across observations
        if len(travel_dates) > 1:
            inconsistency_notes.append(
                f"Multiple travel dates: Cohort safety violation, observations contain multiple travel dates: {travel_dates}."
            )

        # Evaluate PersistenceStatus
        persistence_status = (
            PersistenceStatus.CONSISTENT
            if not inconsistency_notes
            else PersistenceStatus.INCONSISTENT
        )

        # Evaluate CollectionHealthStatus
        if run.status == "FAILED":
            health_status = CollectionHealthStatus.FAILED
        elif run.status == "COMPLETED" and run.records_scraped == 0 and db_observation_count == 0:
            health_status = CollectionHealthStatus.NO_INVENTORY
        elif run.status == "COMPLETED" and db_observation_count > 0:
            if db_observation_count < self.low_data_threshold:
                health_status = CollectionHealthStatus.SUCCESS_WITH_LOW_DATA
            else:
                health_status = CollectionHealthStatus.SUCCESS
        elif run.status == "COMPLETED" and run.records_scraped > 0 and db_observation_count == 0:
            # Scraped quotes failed to persist
            health_status = CollectionHealthStatus.FAILED
        else:
            health_status = CollectionHealthStatus.FAILED

        return TaskAuditRecord(
            run_id=run.run_id,
            source_code=source_code,
            route_code=route_code,
            window_code=window_code,
            target_advance_days=target_advance_days,
            observation_date=observation_date,
            travel_date=travel_date,
            status=run.status,
            health_status=health_status,
            persistence_status=persistence_status,
            records_scraped=run.records_scraped,
            db_observation_count=db_observation_count,
            distinct_fingerprints=distinct_fingerprints,
            valid_count=valid_count,
            invalid_fare_count=invalid_fare_count,
            min_fare=min_fare,
            max_fare=max_fare,
            avg_fare=avg_fare,
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_seconds=duration_seconds,
            error_summary=run.error_summary,
            inconsistency_notes=inconsistency_notes,
        )

    def audit_batch(self, run_ids: List[int]) -> BatchAuditSummary:
        """Audit a batch of collection runs, computing operational metrics and health rates.

        Args:
            run_ids: List of CollectionRun run_ids.

        Returns:
            BatchAuditSummary with aggregate metrics and individual TaskAuditRecords.
        """
        records: List[TaskAuditRecord] = []
        for rid in run_ids:
            try:
                record = self.audit_run(rid)
                records.append(record)
            except Exception as exc:
                logger.error("Error auditing run_id=%d: %s", rid, exc)

        total_runs = len(records)
        success_count = sum(1 for r in records if r.health_status == CollectionHealthStatus.SUCCESS)
        low_data_count = sum(1 for r in records if r.health_status == CollectionHealthStatus.SUCCESS_WITH_LOW_DATA)
        no_inventory_count = sum(1 for r in records if r.health_status == CollectionHealthStatus.NO_INVENTORY)
        failed_count = sum(1 for r in records if r.health_status == CollectionHealthStatus.FAILED)
        consistent_count = sum(1 for r in records if r.persistence_status == PersistenceStatus.CONSISTENT)
        inconsistent_count = sum(1 for r in records if r.persistence_status == PersistenceStatus.INCONSISTENT)
        total_scraped = sum(r.records_scraped for r in records)
        total_obs = sum(r.db_observation_count for r in records)

        durations = [r.duration_seconds for r in records if r.duration_seconds is not None]
        avg_dur = round(sum(durations) / len(durations), 2) if durations else 0.0

        return BatchAuditSummary(
            total_runs=total_runs,
            success_count=success_count,
            low_data_count=low_data_count,
            no_inventory_count=no_inventory_count,
            failed_count=failed_count,
            consistent_count=consistent_count,
            inconsistent_count=inconsistent_count,
            total_records_scraped=total_scraped,
            total_db_observations=total_obs,
            average_duration_seconds=avg_dur,
            records=records,
        )

    def audit_date_and_source(
        self,
        observation_date: date,
        source_code: Optional[str] = None,
    ) -> BatchAuditSummary:
        """Audit all runs created on a specific calendar observation date (in project timezone).

        Args:
            observation_date: Target calendar date.
            source_code: Optional filter by data source code (e.g. 'CLEARTRIP').

        Returns:
            BatchAuditSummary for the matched runs.
        """
        # Define local day window
        start_local = datetime(
            observation_date.year, observation_date.month, observation_date.day, 0, 0, 0, tzinfo=self.timezone
        )
        end_local = start_local + timedelta(days=1)

        stmt = select(CollectionRun.run_id).where(
            CollectionRun.started_at >= start_local,
            CollectionRun.started_at < end_local,
        )

        if source_code:
            from backend.app.db.models.data_source import DataSource
            stmt = stmt.join(DataSource, CollectionRun.source_id == DataSource.source_id).where(
                DataSource.source_code == source_code.upper()
            )

        stmt = stmt.order_by(CollectionRun.run_id.asc())
        run_ids = self.db.scalars(stmt).all()
        return self.audit_batch(list(run_ids))
