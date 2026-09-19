"""Targeted 9-task regression script for EaseMyTrip collector validation fixes."""

from datetime import date, timedelta
import json
import logging
from pathlib import Path
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.db.database import SessionLocal
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route import Route
from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.data_source import DataSource
from backend.collectors.orchestrator import (
    CollectionOrchestrator,
    CollectionTask,
    BasketRoute,
    WindowInfo,
    TaskExecutionResult,
)
from backend.collectors.playwright.browser import BrowserManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("targeted_regression")

# Define exactly the 9 requested tasks
TARGET_SPECS = [
    ("DEL-BOM", "T+1", 1),
    ("DEL-BOM", "T+7", 7),
    ("DEL-BOM", "T+15", 15),
    ("DEL-BOM", "T+30", 30),
    ("DEL-BOM", "T+45", 45),
    ("BLR-DEL", "T+7", 7),
    ("DEL-HYD", "T+7", 7),
    ("IXB-DEL", "T+7", 7),
    ("DEL-IXL", "T+7", 7),
]

def run_regression():
    session = SessionLocal()
    browser_manager = BrowserManager(headless=True)
    results = []

    try:
        orchestrator = CollectionOrchestrator(db=session)
        obs_date = date.today()

        # Build route & window lookup maps from DB
        basket_routes = {r.route_code: r for r in orchestrator.get_active_basket_routes()}
        windows = {w.window_code: w for w in orchestrator.get_active_booking_windows()}

        for idx, (r_code, w_code, adv_days) in enumerate(TARGET_SPECS, start=1):
            basket_route = basket_routes.get(r_code)
            window_info = windows.get(w_code)
            assert basket_route is not None, f"Route {r_code} not found in basket"
            assert window_info is not None, f"Window {w_code} not found in windows"

            travel_date = obs_date + timedelta(days=adv_days)
            task = CollectionTask(
                route=basket_route,
                window=window_info,
                travel_date=travel_date,
                observation_date=obs_date,
            )

            logger.info("=" * 70)
            logger.info(
                "[%d/9] RUNNING TARGETED TASK: %s | Window %s | Travel Date %s",
                idx,
                r_code,
                w_code,
                travel_date,
            )
            logger.info("=" * 70)

            t0 = time.time()
            res: TaskExecutionResult = orchestrator.execute_single_task(
                task=task,
                source_code="EASEMYTRIP",
                browser_manager=browser_manager,
            )
            duration = round(time.time() - t0, 2)

            rec = {
                "index": idx,
                "route": r_code,
                "window": w_code,
                "advance_days": adv_days,
                "observation_date": str(obs_date),
                "travel_date": str(travel_date),
                "run_id": res.run_id,
                "status": res.status,
                "quotes_count": res.quotes_count,
                "inserted_count": res.inserted_count,
                "skipped_count": res.skipped_count,
                "duration_seconds": duration,
                "error": res.error,
            }
            results.append(rec)
            logger.info("Task %d result: %s (Quotes: %d, Inserted: %d, Run ID: %s, Error: %s)",
                        idx, res.status, res.quotes_count, res.inserted_count, res.run_id, res.error)

            time.sleep(2.0)

        # Database Integrity & Audit Verification
        run_ids = [r["run_id"] for r in results if r["run_id"]]
        running_runs = (
            session.query(CollectionRun)
            .filter(CollectionRun.run_id.in_(run_ids), CollectionRun.status == "RUNNING")
            .count()
        )
        all_obs = (
            session.query(FareObservation)
            .filter(FareObservation.run_id.in_(run_ids))
            .all()
        )

        routes_map = {r.route_id: r.route_code for r in session.query(Route).all()}
        windows_map = {w.window_id: w.window_code for w in session.query(BookingWindow).all()}
        sources_map = {s.source_id: s.source_code for s in session.query(DataSource).all()}

        integrity_errors = []
        for r in results:
            rid = r["run_id"]
            run_obs = [o for o in all_obs if o.run_id == rid]
            for o in run_obs:
                if routes_map.get(o.route_id) != r["route"]:
                    integrity_errors.append(f"Route mismatch in run {rid}: {routes_map.get(o.route_id)} != {r['route']}")
                if windows_map.get(o.window_id) != r["window"]:
                    integrity_errors.append(f"Window mismatch in run {rid}: {windows_map.get(o.window_id)} != {r['window']}")
                if sources_map.get(o.data_source_id) not in ("EASEMYTRIP", "EASEMYTRIP_OTA"):
                    integrity_errors.append(f"Source mismatch in run {rid}: {sources_map.get(o.data_source_id)}")
                if str(o.travel_date) != r["travel_date"]:
                    integrity_errors.append(f"Travel date mismatch in run {rid}: {o.travel_date} != {r['travel_date']}")

        out_data = {
            "tasks": results,
            "total_tasks": len(results),
            "succeeded": sum(1 for r in results if r["status"] == "COMPLETED"),
            "failed": sum(1 for r in results if r["status"] == "FAILED"),
            "total_quotes": sum(r["quotes_count"] for r in results),
            "total_persisted": len(all_obs),
            "running_runs_count": running_runs,
            "integrity_errors": integrity_errors,
        }

        with open("scratch/targeted_regression_results.json", "w", encoding="utf-8") as f:
            json.dump(out_data, f, indent=2)

        print("\n" + "=" * 80)
        print("TARGETED REGRESSION RESULTS SUMMARY:")
        print("=" * 80)
        print(json.dumps(out_data, indent=2))

    finally:
        browser_manager.close()
        session.close()

if __name__ == "__main__":
    run_regression()
