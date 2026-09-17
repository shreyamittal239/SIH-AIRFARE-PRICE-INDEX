"""Targeted 12-task revalidation script for SpiceJet collector zero-inventory fix.

Executes the 11 previously failed tasks + 1 known-good control task:
A. BLR-DEL: T+1 (2026-09-17), T+7 (2026-09-23), T+15 (2026-10-01), T+30 (2026-10-16)
B. DEL-SXR: T+30 (2026-10-16)
C. IXB-DEL: T+1 (2026-09-17), T+7 (2026-09-23), T+15 (2026-10-01)
D. DEL-IXL: T+15 (2026-10-01), T+30 (2026-10-16), T+45 (2026-10-31)
E. DEL-BOM: T+1 (2026-09-17) [Control / Known-Good Case]

Total: 12 targeted tasks.
"""

from datetime import date, datetime, timedelta, timezone
import json
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
from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.data_source import DataSource
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route import Route
from backend.collectors.orchestrator import (
    CollectionOrchestrator,
    CollectionTask,
    CollectionStatus,
    TaskExecutionResult,
)
from backend.collectors.playwright.browser import BrowserManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scratch/spicejet_targeted_12.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("spicejet_targeted_12")

TARGET_SPEC = [
    ("BLR-DEL", "T+1", 1, date(2026, 9, 17), "BLR", "DEL"),
    ("BLR-DEL", "T+7", 7, date(2026, 9, 23), "BLR", "DEL"),
    ("BLR-DEL", "T+15", 15, date(2026, 10, 1), "BLR", "DEL"),
    ("BLR-DEL", "T+30", 30, date(2026, 10, 16), "BLR", "DEL"),
    ("DEL-SXR", "T+30", 30, date(2026, 10, 16), "DEL", "SXR"),
    ("IXB-DEL", "T+1", 1, date(2026, 9, 17), "IXB", "DEL"),
    ("IXB-DEL", "T+7", 7, date(2026, 9, 23), "IXB", "DEL"),
    ("IXB-DEL", "T+15", 15, date(2026, 10, 1), "IXB", "DEL"),
    ("DEL-IXL", "T+15", 15, date(2026, 10, 1), "DEL", "IXL"),
    ("DEL-IXL", "T+30", 30, date(2026, 10, 16), "DEL", "IXL"),
    ("DEL-IXL", "T+45", 45, date(2026, 10, 31), "DEL", "IXL"),
    ("DEL-BOM", "T+1", 1, date(2026, 9, 17), "DEL", "BOM"),  # Known-good control
]


def run_targeted_revalidation():
    logger.info("=" * 80)
    logger.info("STARTING TARGETED 12-TASK SPICEJET REVALIDATION")
    logger.info("=" * 80)

    session = SessionLocal()
    orchestrator = CollectionOrchestrator(db=session)

    # Load routes and windows map
    all_routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    route_map = {r.route_code: r for r in all_routes}
    all_windows = orchestrator.get_active_booking_windows()
    window_map = {w.window_code: w for w in all_windows}

    obs_date = date(2026, 9, 16)
    logger.info("Observation Date: %s", obs_date)

    tasks: list[CollectionTask] = []
    for r_code, w_code, adv_days, t_date, orig, dest in TARGET_SPEC:
        route = route_map[r_code]
        window = window_map[w_code]
        task = CollectionTask(
            route=route,
            window=window,
            travel_date=t_date,
            observation_date=obs_date,
        )
        tasks.append(task)

    logger.info("Constructed %d targeted tasks.", len(tasks))

    bm = BrowserManager(headless=False, timeout_ms=40000)
    results_records = []
    start_total_time = time.time()

    try:
        for idx, task in enumerate(tasks, start=1):
            is_control = (task.route.route_code == "DEL-BOM")
            tag = "CONTROL" if is_control else "PREVIOUSLY FAILED"
            logger.info(
                "\n" + "#" * 80 + "\n"
                f">>> [{idx}/12 | {tag}] Route: {task.route.route_code} | Window: {task.window.window_code} | Travel Date: {task.travel_date} <<<\n"
                + "#" * 80
            )

            t0 = time.time()
            res: TaskExecutionResult = orchestrator.execute_single_task(
                task=task,
                source_code="SPICEJET",
                browser_manager=bm,
            )
            duration = time.time() - t0

            # Dispose context cleanly
            if hasattr(bm, "_contexts"):
                for ctx in list(bm._contexts):
                    try:
                        bm.close_context(ctx)
                    except Exception:
                        pass

            is_zero_inv = (res.collection_status == CollectionStatus.SUCCESS_NO_INVENTORY)
            error_cat = None
            if res.status == "FAILED":
                err_lower = (res.error or "").lower()
                if "net::" in err_lower or "err_name_not_resolved" in err_lower or "dns" in err_lower:
                    error_cat = "NETWORK_FAILURE"
                elif "timeout" in err_lower:
                    error_cat = "SCRAPE_DOM_TIMEOUT"
                else:
                    error_cat = "OTHER_FAILURE"

            logger.info(
                f"<<< [{idx}/12] Outcome: Run #{res.run_id} | Status={res.status} | "
                f"CollectionStatus={res.collection_status} | ZeroInv={is_zero_inv} | "
                f"Quotes={res.quotes_count} | Persisted={res.inserted_count} | "
                f"Attempts={res.attempts} | Retried={res.retried} | "
                f"Recovered={res.recovered_on_retry} | ErrorCat={error_cat} | Dur={duration:.2f}s >>>"
            )

            rec = {
                "task_index": idx,
                "is_control": is_control,
                "route_code": task.route.route_code,
                "origin": task.route.origin_code,
                "destination": task.route.destination_code,
                "window_code": task.window.window_code,
                "target_advance_days": task.window.target_advance_days,
                "travel_date": task.travel_date.isoformat(),
                "observation_date": task.observation_date.isoformat(),
                "run_id": res.run_id,
                "attempts": res.attempts,
                "retried": res.retried,
                "recovered_on_retry": res.recovered_on_retry,
                "final_status": res.status,
                "collection_status": str(res.collection_status),
                "is_zero_inventory": is_zero_inv,
                "quotes_count": res.quotes_count,
                "inserted_count": res.inserted_count,
                "error": res.error,
                "error_category": error_cat,
                "duration_seconds": round(duration, 2),
            }
            results_records.append(rec)

            time.sleep(2.0)

    finally:
        bm.close()
        session.close()

    total_duration = time.time() - start_total_time
    logger.info(f"\nAll 12 targeted tasks completed in {total_duration:.2f}s.")

    out_file = Path("scratch/spicejet_targeted_12_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results_records, f, indent=2)
    logger.info(f"Saved results to {out_file}")

    return results_records


if __name__ == "__main__":
    run_targeted_revalidation()
