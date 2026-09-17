"""Execute controlled 10-task live collection test across 5 DGCA routes and 2 booking windows.

Audits all runs in PostgreSQL via CollectionAuditService and verifies persistence consistency.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.db.database import SessionLocal
from backend.collectors.collection_audit import (
    CollectionAuditService,
    CollectionHealthStatus,
    PersistenceStatus,
)
from backend.collectors.orchestrator import CollectionOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("controlled_10_task_audit")


def main() -> None:
    session = SessionLocal()
    try:
        orchestrator = CollectionOrchestrator(db=session)
        audit_service = CollectionAuditService(db=session)

        # 1. Dynamically query 5 active DGCA routes and 2 booking windows
        active_routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
        selected_routes = active_routes[:5]

        active_windows = orchestrator.get_active_booking_windows()
        selected_windows = active_windows[:2]

        logger.info("=== Controlled 10-Task Collection Live Test ===")
        logger.info("Selected %d Routes from DB route_weights:", len(selected_routes))
        for r in selected_routes:
            logger.info("  Route %d: %s (id=%d, weight=%.4f)", r.display_order if hasattr(r, 'display_order') else 0, r.route_code, r.route_id, r.weight)

        logger.info("Selected %d Booking Windows from DB booking_windows:", len(selected_windows))
        for w in selected_windows:
            logger.info("  Window %s: %s (id=%d, advance_days=%d)", w.window_code, w.target_advance_days, w.window_id, w.target_advance_days)

        # 2. Build 10 tasks (5 routes × 2 windows)
        tasks = orchestrator.build_task_matrix(selected_routes, selected_windows)
        logger.info("Constructed %d collection tasks.", len(tasks))
        for idx, t in enumerate(tasks, start=1):
            logger.info("  Task %d: %s | Window: %s | Travel Date: %s", idx, t.route.route_code, t.window.window_code, t.travel_date)

        # 3. Execute batch sequentially on CLEARTRIP with polite pacing (3s)
        source_code = "CLEARTRIP"
        logger.info("Starting batch execution on %s with delay=3.0s...", source_code)
        batch_summary = orchestrator.run_collection(
            source_code=source_code,
            tasks=tasks,
            delay_seconds=3.0,
        )

        logger.info("=== Batch Execution Completed ===")
        logger.info("Total tasks: %d, Completed: %d, Failed: %d, Total inserted: %d",
                    batch_summary.total_tasks, batch_summary.completed_tasks,
                    batch_summary.failed_tasks, batch_summary.total_inserted)

        run_ids = [res.run_id for res in batch_summary.results if res.run_id is not None]
        logger.info("Collected Run IDs: %s", run_ids)

        # 4. Audit all 10 runs in PostgreSQL using CollectionAuditService
        audit_summary = audit_service.audit_batch(run_ids)

        # 5. Output formatted report
        print("\n" + "=" * 80)
        print("COLLECTION MONITORING & PERSISTENCE AUDIT REPORT (10 TASKS)")
        print("=" * 80 + "\n")
        print(audit_summary.to_markdown_table())
        print("\n" + "=" * 80)
        print("AGGREGATE OPERATIONAL METRICS")
        print("=" * 80)
        print(f"Total Runs Evaluated:        {audit_summary.total_runs}")
        print(f"Success Count (>=5 quotes):   {audit_summary.success_count}")
        print(f"Success With Low Data (1-4): {audit_summary.low_data_count}")
        print(f"No Inventory Count (0):      {audit_summary.no_inventory_count}")
        print(f"Failed Count:                {audit_summary.failed_count}")
        print(f"Success Rate:                {audit_summary.success_rate:.1f}%")
        print(f"No Inventory Rate:           {audit_summary.no_inventory_rate:.1f}%")
        print(f"Failure Rate:                {audit_summary.failure_rate:.1f}%")
        print(f"Consistency Rate:            {audit_summary.consistency_rate:.1f}%")
        print(f"Total Quotes Scraped:        {audit_summary.total_records_scraped}")
        print(f"Total DB Observations:       {audit_summary.total_db_observations}")
        print(f"Average Run Duration:        {audit_summary.average_duration_seconds:.2f} seconds")
        print("=" * 80 + "\n")

        # Inconsistency details if any
        inconsistent_runs = [r for r in audit_summary.records if r.persistence_status == PersistenceStatus.INCONSISTENT]
        if inconsistent_runs:
            print("WARNING: Inconsistent runs detected:")
            for ir in inconsistent_runs:
                print(f"  Run ID {ir.run_id} ({ir.route_code} {ir.window_code}): {ir.inconsistency_notes}")
        else:
            print("PERFECT CONSISTENCY: All runs verified 100% consistent with database persistence invariants.")

    finally:
        session.close()


if __name__ == "__main__":
    main()
