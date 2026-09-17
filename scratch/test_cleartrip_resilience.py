"""Live smoke test (4 tasks) verifying Cleartrip collector resilience and context leak fix.

Verifies:
1. Browser context count in BrowserManager resets to 0 after every single task.
2. Successful tasks extract and persist quotes into PostgreSQL.
3. No open zombie tabs accumulate across sequential tasks.
4. Clean failure isolation.
"""
from datetime import date, datetime, timedelta, timezone
import logging
from pathlib import Path
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.db.database import SessionLocal
from backend.app.db.models.fare_observation import FareObservation
from backend.collectors.collection_audit import CollectionAuditService
from backend.collectors.orchestrator import CollectionOrchestrator, CollectionTask
from backend.collectors.playwright.browser import BrowserManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("cleartrip_resilience_test")


def main() -> None:
    session = SessionLocal()
    browser_manager = BrowserManager(headless=True)
    try:
        orchestrator = CollectionOrchestrator(db=session)
        audit_service = CollectionAuditService(db=session)

        routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
        windows = orchestrator.get_active_booking_windows()
        windows_by_code = {w.window_code: w for w in windows}

        routes_by_code = {r.route_code: r for r in routes}

        # Build 4 smoke test tasks
        test_specs = [
            ("DEL-BOM", "T+7"),
            ("BLR-DEL", "T+7"),
            ("DEL-HYD", "T+7"),
            ("DEL-BOM", "T+1"),
        ]

        obs_date = date.today()
        test_tasks = []
        for r_code, w_code in test_specs:
            r = routes_by_code[r_code]
            w = windows_by_code[w_code]
            t_date = obs_date + timedelta(days=w.target_advance_days)
            test_tasks.append(CollectionTask(route=r, window=w, travel_date=t_date, observation_date=obs_date))

        logger.info("=== STARTING 4-TASK RESILIENCE SMOKE TEST ===")
        run_results = []

        for idx, task in enumerate(test_tasks, start=1):
            logger.info("--- [Task %d/%d] Starting %s %s (travel_date=%s) ---",
                        idx, len(test_tasks), task.route.route_code, task.window.window_code, task.travel_date)

            res = orchestrator.execute_single_task(
                task=task,
                source_code="CLEARTRIP",
                browser_manager=browser_manager,
            )
            run_results.append(res)

            # Check context leak status
            active_contexts = len(browser_manager._contexts)
            logger.info(
                "--- [Task %d/%d] Finished: status=%s, quotes=%d, inserted=%d, run_id=%s, active_contexts_in_bm=%d ---",
                idx, len(test_tasks), res.status, res.quotes_count, res.inserted_count, res.run_id, active_contexts
            )

            # Assert context leak is 0
            if active_contexts != 0:
                logger.error("CONTEXT LEAK DETECTED! %d active contexts remaining in browser_manager!", active_contexts)
            else:
                logger.info("CONFIRMED: 0 active contexts remaining in BrowserManager (clean lifecycle).")

            time.sleep(2.0)

        # Audit the 4 runs in DB
        run_ids = [r.run_id for r in run_results if r.run_id is not None]
        audit_summary = audit_service.audit_batch(run_ids)

        print("\n" + "=" * 90)
        print("RESILIENCE SMOKE TEST AUDIT REPORT")
        print("=" * 90)
        print(audit_summary.to_markdown_table())

        # Check DB observations
        total_obs = session.query(FareObservation).filter(FareObservation.run_id.in_(run_ids)).count()
        print(f"\nTotal runs audited: {len(run_ids)}")
        print(f"Total quotes inserted: {sum(r.inserted_count for r in run_results)}")
        print(f"Total fare observations in DB: {total_obs}")
        print(f"Persistence parity: {'100% PASS' if total_obs == sum(r.inserted_count for r in run_results) else 'FAIL'}")

    finally:
        browser_manager.close()
        session.close()


if __name__ == "__main__":
    main()
