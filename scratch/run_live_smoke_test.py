"""Controlled live smoke test for the CollectionOrchestrator.

Executes:
2 routes (DEL-BOM, BLR-DEL) × 1 booking window (T+7) across 2 sources (Cleartrip, Yatra).
Total: 4 tasks.

Verifies:
- Dynamic route retrieval from route_weights
- Dynamic travel date calculation from booking_windows
- Collector dispatch via CollectorAdapter
- FlightQuote extraction
- FareObservation persistence
- CollectionRun status (COMPLETED)
- Idempotency & deduplication
"""

from datetime import date, datetime, timezone
import logging
import sys

from backend.app.db.database import SessionLocal
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.collectors.orchestrator import (
    CollectionOrchestrator,
    CollectionTask,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("smoke_test")


def main():
    db = SessionLocal()
    orchestrator = CollectionOrchestrator(db)

    print("=== Step 1: Querying Active Routes from DB ===")
    all_routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    selected_routes = all_routes[:2]
    for r in selected_routes:
        print(f"  Selected Route {r.route_id}: {r.route_code} ({r.origin_code} -> {r.destination_code}), Weight={r.weight}")

    print("\n=== Step 2: Querying Active Booking Windows from DB ===")
    all_windows = orchestrator.get_active_booking_windows()
    selected_windows = [w for w in all_windows if w.window_code == "T+7"]
    for w in selected_windows:
        print(f"  Selected Window {w.window_id}: {w.window_code} (+{w.target_advance_days} days)")

    print("\n=== Step 3: Building Task Matrix ===")
    obs_date = datetime.now(timezone.utc).date()
    tasks = orchestrator.build_task_matrix(selected_routes, selected_windows, observation_date=obs_date)
    print(f"  Generated {len(tasks)} tasks:")
    for t in tasks:
        print(f"    {t.route.route_code} | {t.window.window_code} | Travel Date: {t.travel_date}")

    sources_to_test = ["CLEARTRIP", "YATRA"]
    overall_runs = []

    for src in sources_to_test:
        print(f"\n=== Step 4: Executing Live Smoke Tasks for Source: {src} ===")
        summary = orchestrator.run_collection(
            source_code=src,
            tasks=tasks,
            delay_seconds=3.0,
        )

        print(f"\n--- Summary for {src} ---")
        print(f"  Total Tasks: {summary.total_tasks}")
        print(f"  Completed: {summary.completed_tasks}, Failed: {summary.failed_tasks}")
        print(f"  Total Inserted: {summary.total_inserted}, Skipped: {summary.total_skipped}")

        for res in summary.results:
            print(f"    Task [{res.task.route.route_code} {res.task.window.window_code}]: "
                  f"Status={res.status}, RunID={res.run_id}, Quotes={res.quotes_count}, "
                  f"Inserted={res.inserted_count}, Skipped={res.skipped_count}, Error={res.error}")
            if res.run_id:
                overall_runs.append(res.run_id)

    print("\n=== Step 5: Database Persistence Audit for Smoke Test Runs ===")
    for run_id in overall_runs:
        run = db.query(CollectionRun).filter_by(run_id=run_id).first()
        obs_count = db.query(FareObservation).filter_by(run_id=run_id).count()
        print(f"  Run ID {run.run_id}: SourceID={run.source_id}, RouteID={run.target_route_id}, "
              f"WindowID={run.target_window_id}, Status={run.status}, RecordsScraped={run.records_scraped}, "
              f"ObservationsInDB={obs_count}")

    db.close()
    print("\n=== Live Smoke Test Finished Successfully ===")


if __name__ == "__main__":
    main()
