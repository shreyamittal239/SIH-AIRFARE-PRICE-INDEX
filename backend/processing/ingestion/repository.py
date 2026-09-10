"""Transactional persistence repository for collection runs and observations.

Handles idempotent batch persistence, auditing, intra-run duplicate filtering,
and transaction rollback semantics.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.processing.ingestion.fare_mapper import map_quote_to_observation
from backend.processing.ingestion.resolver import DimensionResolver

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    """Summary metrics of an ingestion execution."""

    run_id: int
    total_quotes: int = 0
    inserted: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)
    observations: List[FareObservation] = field(default_factory=list)


class IngestionRepository:
    """Repository managing CollectionRun auditing and FareObservation persistence."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.resolver = DimensionResolver(db)

    def start_collection_run(
        self,
        source_id: int,
        target_route_id: Optional[int] = None,
        target_window_id: Optional[int] = None,
    ) -> CollectionRun:
        """Create and persist a new CollectionRun with status RUNNING."""
        run = CollectionRun(
            source_id=source_id,
            target_route_id=target_route_id,
            target_window_id=target_window_id,
            started_at=datetime.now(timezone.utc),
            status="RUNNING",
            records_scraped=0,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        logger.info("Started CollectionRun: run_id=%d, source_id=%d", run.run_id, source_id)
        return run

    def complete_collection_run(
        self,
        run: CollectionRun,
        status: str = "COMPLETED",
        error_summary: Optional[str] = None,
    ) -> None:
        """Update a CollectionRun upon finishing collection."""
        run.completed_at = datetime.now(timezone.utc)
        run.status = status
        if error_summary:
            run.error_summary = error_summary
        self.db.commit()
        logger.info(
            "Completed CollectionRun: run_id=%d, status=%s, records=%d",
            run.run_id,
            status,
            run.records_scraped,
        )

    def ingest_quotes(
        self,
        quotes: List[FlightQuote],
        run: Optional[CollectionRun] = None,
        source_id: Optional[int] = None,
    ) -> IngestionResult:
        """Map and persist a batch of FlightQuote objects into PostgreSQL.

        Args:
            quotes: List of validated FlightQuotes.
            run: Optional existing CollectionRun. If None, one will be created.
            source_id: Optional source_id override for creating a run.

        Returns:
            IngestionResult detailing processed, inserted, and skipped counts.
        """
        if not quotes and run is None:
            raise ValueError("Cannot ingest empty quotes list without an existing run.")

        # Determine or create CollectionRun
        if run is None:
            first_quote = quotes[0]
            resolved_source = self.resolver.resolve_data_source(first_quote.source)
            resolved_route = self.resolver.resolve_route(first_quote.origin, first_quote.destination)
            adv_days = max(0, (first_quote.travel_date - first_quote.observed_at.date()).days)
            resolved_window = self.resolver.resolve_booking_window(adv_days)

            run = self.start_collection_run(
                source_id=source_id or resolved_source.source_id,
                target_route_id=resolved_route.route_id,
                target_window_id=resolved_window.window_id,
            )

        result = IngestionResult(run_id=run.run_id, total_quotes=len(quotes))

        # Query existing fingerprints for this run & source to guarantee idempotency
        stmt = select(FareObservation.fingerprint_hash).where(
            FareObservation.run_id == run.run_id,
            FareObservation.data_source_id == run.source_id,
        )
        existing_fingerprints = set(self.db.scalars(stmt).all())
        batch_fingerprints = set()

        try:
            for quote in quotes:
                try:
                    dims = self.resolver.resolve_dimensions(quote)
                    obs = map_quote_to_observation(quote, run.run_id, dims)

                    # Deduplication check: intra-run uniqueness
                    if (
                        obs.fingerprint_hash in existing_fingerprints
                        or obs.fingerprint_hash in batch_fingerprints
                    ):
                        logger.warning(
                            "Skipping duplicate observation in run %d: flight=%s, fp=%s",
                            run.run_id,
                            obs.flight_number,
                            obs.fingerprint_hash[:12],
                        )
                        result.skipped += 1
                        continue

                    batch_fingerprints.add(obs.fingerprint_hash)
                    self.db.add(obs)
                    result.observations.append(obs)
                    result.inserted += 1

                except Exception as map_err:
                    err_msg = f"Failed mapping quote {quote.flight_number}: {map_err}"
                    logger.error(err_msg)
                    result.failed += 1
                    result.errors.append(err_msg)

            # Update run metrics and commit transactionally
            run.records_scraped += result.inserted
            self.complete_collection_run(
                run,
                status="FAILED" if (result.failed > 0 and result.inserted == 0) else "COMPLETED",
                error_summary="; ".join(result.errors) if result.errors else None,
            )

            logger.info(
                "Ingestion completed for run %d: %d inserted, %d skipped, %d failed",
                run.run_id,
                result.inserted,
                result.skipped,
                result.failed,
            )
            return result

        except Exception as db_err:
            self.db.rollback()
            logger.error("Transactional rollback during ingestion: %s", db_err)
            self.complete_collection_run(run, status="FAILED", error_summary=str(db_err))
            raise
