"""Targeted re-execution and database audit of the 3 previously failed SpiceJet tasks:
1. DEL-HYD T+45 (2026-10-31)
2. CCU-BOM T+7 (2026-09-23)
3. CCU-BOM T+15 (2026-10-01)
Observation Date: 2026-09-16
"""

from datetime import date, datetime, timezone
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

from sqlalchemy import select
from backend.app.db.database import SessionLocal
from backend.app.db.models.route import Route
from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.data_source import DataSource
from backend.collectors.orchestrator import (
    CollectionOrchestrator,
    CollectionTask,
    BasketRoute,
    WindowInfo,
    TaskExecutionResult,
    CollectionStatus,
)
from backend.collectors.playwright.browser import BrowserManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rerun_spicejet_3")


def run():
    session = SessionLocal()
    orchestrator = CollectionOrchestrator(db=session)

    # 1. Load routes & windows
    routes_db = {r.route_code: r for r in session.scalars(select(Route)).all()}
    windows_db = {w.window_code: w for w in session.scalars(select(BookingWindow)).all()}
    spicejet_source = session.scalars(select(DataSource).where(DataSource.source_name == "SpiceJet Direct")).first()
    assert spicejet_source is not None, "SpiceJet Direct source not found in DB"

    obs_date = date(2026, 9, 16)

    # 1. Load active basket routes & booking windows
    routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    windows = orchestrator.get_active_booking_windows()
    tasks_all = orchestrator.build_task_matrix(routes, windows, observation_date=obs_date)

    target_keys = {
        ("DEL-HYD", "T+45"),
        ("CCU-BOM", "T+7"),
        ("CCU-BOM", "T+15"),
    }
    tasks = [t for t in tasks_all if (t.route.route_code, t.window.window_code) in target_keys]
    assert len(tasks) == 3, f"Expected 3 tasks, found {len(tasks)}"

    bm = BrowserManager(headless=False, timeout_ms=40000)
    bm.launch()

    results = []
    created_run_ids = []

    try:
        for idx, task in enumerate(tasks, 1):
            logger.info("=" * 70)
            logger.info(
                f"RERUNNING [{idx}/3]: SPICEJET | Route: {task.route.route_code} | Window: {task.window.window_code} | Date: {task.travel_date}"
            )
            logger.info("=" * 70)

            t_start = time.time()
            res: TaskExecutionResult = orchestrator.execute_single_task(
                task=task,
                source_code="SPICEJET",
                browser_manager=bm,
            )
            t_dur = time.time() - t_start

            # Close open contexts
            if hasattr(bm, "_contexts"):
                for ctx in list(bm._contexts):
                    try:
                        bm.close_context(ctx)
                    except Exception:
                        pass

            logger.info(
                f"<<< OUTCOME [{idx}/3]: status={res.status}, coll_status={res.collection_status}, "
                f"quotes={res.quotes_count}, inserted={res.inserted_count}, run_id={res.run_id}, dur={t_dur:.2f}s >>>"
            )

            rec = {
                "route_code": task.route.route_code,
                "window_code": task.window.window_code,
                "travel_date": str(task.travel_date),
                "observation_date": str(task.observation_date),
                "run_id": res.run_id,
                "status": res.status,
                "collection_status": str(res.collection_status.value) if res.collection_status else None,
                "quotes_count": res.quotes_count,
                "inserted_count": res.inserted_count,
                "skipped_count": res.skipped_count,
                "retried": res.retried,
                "recovered_on_retry": (res.retried and res.status == "COMPLETED"),
                "error": res.error,
                "duration_seconds": round(t_dur, 2),
            }
            results.append(rec)
            if res.run_id:
                created_run_ids.append(res.run_id)

            time.sleep(2.0)

    finally:
        bm.close()

    # Database Validation (Read-Only)
    logger.info("Performing database integrity validation across run_ids: %s", created_run_ids)
    runs = session.scalars(select(CollectionRun).where(CollectionRun.run_id.in_(created_run_ids))).all()
    observations = session.scalars(select(FareObservation).where(FareObservation.run_id.in_(created_run_ids))).all()

    audit_summary = {
        "runs_count": len(runs),
        "observations_count": len(observations),
        "orphan_running_runs": len([r for r in runs if r.status == "RUNNING"]),
        "route_mismatches": 0,
        "window_mismatches": 0,
        "source_mismatches": 0,
        "travel_date_mismatches": 0,
        "invalid_fares": 0,
        "fingerprints": [],
    }

    run_map = {r.run_id: r for r in runs}

    for obs in observations:
        r = run_map.get(obs.run_id)
        if r:
            if obs.route_id != r.target_route_id:
                audit_summary["route_mismatches"] += 1
            if obs.window_id != r.target_window_id:
                audit_summary["window_mismatches"] += 1
            if obs.data_source_id != r.source_id:
                audit_summary["source_mismatches"] += 1

        if obs.advance_days != (obs.travel_date - obs.observed_at.date()).days:
            audit_summary["travel_date_mismatches"] += 1

        if obs.total_fare <= 0 or obs.quality_status != "VALID":
            audit_summary["invalid_fares"] += 1

        audit_summary["fingerprints"].append(obs.fingerprint_hash)

    audit_summary["duplicate_fingerprints"] = len(audit_summary["fingerprints"]) - len(set(audit_summary["fingerprints"]))
    audit_summary["persistence_parity"] = (sum(r["inserted_count"] for r in results) == len(observations))

    full_output = {
        "task_results": results,
        "audit_summary": audit_summary,
    }

    out_file = Path("scratch/spicejet_3_rerun_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(full_output, f, indent=2)
    logger.info("Saved rerun results to %s", out_file)
    session.close()


if __name__ == "__main__":
    run()
