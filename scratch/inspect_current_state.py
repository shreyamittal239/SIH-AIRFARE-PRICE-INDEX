
"""Step 1: Inspect current database state for the 125-task matrix.

Maps every task (1 to 125) to its CollectionRun ID, route, booking window, status, observation count.
DO NOT MODIFY ANYTHING.
"""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys

# Safe stdout reconfigure
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from backend.app.db.database import SessionLocal
from backend.collectors.orchestrator import CollectionOrchestrator

session = SessionLocal()
try:
    orchestrator = CollectionOrchestrator(db=session)

    # 1. Dynamically load routes and windows as done in the matrix
    routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    windows = orchestrator.get_active_booking_windows()
    windows.sort(key=lambda w: w.display_order)

    # Build the 125 tasks
    tasks = orchestrator.build_task_matrix(routes, windows)
    print(f"Total tasks dynamically generated: {len(tasks)}")

    # Query all collection runs from this cycle (source_id = 65, run_id >= 297)
    # also query the observations count per run
    sql_runs = text("""
        SELECT 
            cr.run_id,
            cr.source_id,
            cr.target_route_id,
            cr.target_window_id,
            r.route_code,
            bw.window_code,
            cr.status,
            cr.records_scraped,
            cr.started_at,
            cr.completed_at,
            cr.error_summary,
            COUNT(fo.observation_id) AS obs_count
        FROM collection_runs cr
        JOIN routes r ON cr.target_route_id = r.route_id
        JOIN booking_windows bw ON cr.target_window_id = bw.window_id
        LEFT JOIN fare_observations fo ON fo.run_id = cr.run_id
        WHERE cr.run_id >= 297 AND cr.source_id = 65
        GROUP BY cr.run_id, cr.source_id, cr.target_route_id, cr.target_window_id, r.route_code, bw.window_code, cr.status, cr.records_scraped, cr.started_at, cr.completed_at, cr.error_summary
        ORDER BY cr.run_id ASC;
    """)
    db_runs = session.execute(sql_runs).mappings().all()
    print(f"Total collection runs found in DB (run_id >= 297): {len(db_runs)}")

    # Index db runs by (route_id, window_id)
    # If multiple runs exist for same task, track them in order
    runs_by_route_window = {}
    for r in db_runs:
        key = (r["target_route_id"], r["target_window_id"])
        if key not in runs_by_route_window:
            runs_by_route_window[key] = []
        runs_by_route_window[key].append(r)

    # Map each task 1 to 125
    print("\n" + "=" * 115)
    print(f"{'Task #':<7} | {'Run ID':<8} | {'Route':<9} | {'Window':<8} | {'Travel Date':<12} | {'Status':<12} | {'Scraped':<8} | {'Persisted':<10} | {'Notes'}")
    print("-" * 115)

    completed_count = 0
    failed_count = 0
    running_count = 0
    not_started_count = 0

    task_table_data = []

    for idx, task in enumerate(tasks, start=1):
        key = (task.route.route_id, task.window.window_id)
        runs_for_task = runs_by_route_window.get(key, [])
        
        if not runs_for_task:
            run_id_str = "N/A"
            status_str = "NOT_STARTED"
            scraped_str = "0"
            persisted_str = "0"
            notes = "Pending execution"
            not_started_count += 1
        else:
            # Use the latest run for this task
            latest_run = runs_for_task[-1]
            run_id_str = str(latest_run["run_id"])
            status_str = latest_run["status"]
            scraped_str = str(latest_run["records_scraped"])
            persisted_str = str(latest_run["obs_count"])
            notes = ""
            if status_str == "COMPLETED":
                completed_count += 1
                notes = "Completed successfully"
            elif status_str == "FAILED":
                failed_count += 1
                err = (latest_run["error_summary"] or "")[:25]
                notes = f"Failed ({err})"
            elif status_str == "RUNNING":
                running_count += 1
                notes = "Interrupted mid-flight (0 obs)"
            else:
                notes = status_str

        row_dict = {
            "task_num": idx,
            "run_id": run_id_str,
            "route_code": task.route.route_code,
            "window_code": task.window.window_code,
            "travel_date": str(task.travel_date),
            "status": status_str,
            "scraped": scraped_str,
            "persisted": persisted_str,
            "notes": notes,
        }
        task_table_data.append(row_dict)

        # Print all initiated tasks and a summary of not started
        if idx <= 35 or idx == 125:
            print(f"{idx:<7} | {run_id_str:<8} | {task.route.route_code:<9} | {task.window.window_code:<8} | {str(task.travel_date):<12} | {status_str:<12} | {scraped_str:<8} | {persisted_str:<10} | {notes}")
        elif idx == 36:
            print(f"... Tasks 36 to 124 are NOT_STARTED (Pending execution) ...")

    print("-" * 115)
    print(f"\nTask Status Summary:")
    print(f"  Total planned tasks:      {len(tasks)}")
    print(f"  Completed successfully:   {completed_count}")
    print(f"  Failed:                   {failed_count}")
    print(f"  Interrupted (RUNNING):    {running_count}")
    print(f"  Not started:              {not_started_count}")

    # Output detailed JSON for Step 1
    with open("scratch/step1_inspection.json", "w", encoding="utf-8") as f:
        json.dump(task_table_data, f, indent=2)
    print("\nSaved inspection details to scratch/step1_inspection.json")

finally:
    session.close()
