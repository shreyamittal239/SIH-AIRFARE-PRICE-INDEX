"""Resume and complete the 125-task Cleartrip collection matrix.

Safely recovers the interrupted collection:
1. Verifies Run #328 status.
2. Identifies the 22 already completed tasks and skips them.
3. Identifies the 103 incomplete tasks (9 failed + 1 interrupted + 93 new).
4. Executes the 103 tasks sequentially on Cleartrip with:
   - 3.0s normal delay
   - 1 retry for transient technical failures with 5.0s backoff
   - Single shared Chromium browser context
   - Full persistence and audit
5. Audits all 125 final runs in PostgreSQL.
6. Generates complete 125-cell Route × Window matrix and full engineering statistics.
"""

from datetime import date, datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import statistics
import sys
import time

# Ensure safe UTF-8 console output
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from backend.app.db.database import SessionLocal
from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.data_source import DataSource
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route import Route
from backend.collectors.collection_audit import (
    CollectionAuditService,
    CollectionHealthStatus,
    PersistenceStatus,
)
from backend.collectors.orchestrator import (
    CollectionOrchestrator,
    CollectionTask,
    TaskExecutionResult,
)
from backend.collectors.playwright.browser import BrowserManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("cleartrip_matrix_resume")


def find_existing_successful_run(session, task: CollectionTask):
    """Check if task already has a successful completed run in this cycle."""
    sql = text("""
        SELECT 
            cr.run_id,
            cr.records_scraped,
            cr.status,
            COUNT(fo.observation_id) AS obs_count
        FROM collection_runs cr
        JOIN data_sources ds ON cr.source_id = ds.source_id
        LEFT JOIN fare_observations fo ON fo.run_id = cr.run_id
        WHERE cr.target_route_id = :route_id
          AND cr.target_window_id = :window_id
          AND ds.source_code IN ('CLEARTRIP', 'CLEARTRIP_OTA')
          AND cr.status = 'COMPLETED'
          AND cr.run_id >= 297
        GROUP BY cr.run_id, cr.records_scraped, cr.status
        HAVING COUNT(fo.observation_id) > 0 OR cr.records_scraped = 0
        ORDER BY cr.run_id DESC
        LIMIT 1;
    """)
    res = session.execute(sql, {
        "route_id": task.route.route_id,
        "window_id": task.window.window_id,
    }).mappings().one_or_none()
    return res


def main() -> None:
    session = SessionLocal()
    browser_manager = BrowserManager(headless=True)
    try:
        orchestrator = CollectionOrchestrator(db=session)
        audit_service = CollectionAuditService(db=session)

        # ----------------------------------------------------------------------
        # STEP 1 & 2: CONFIRM RUN #328 STATE
        # ----------------------------------------------------------------------
        run_328 = session.execute(text("SELECT run_id, status, error_summary FROM collection_runs WHERE run_id = 328")).mappings().one_or_none()
        if run_328 and run_328["status"] == "RUNNING":
            logger.info("Setting Run 328 from RUNNING to FAILED...")
            session.execute(text("""
                UPDATE collection_runs
                SET status = 'FAILED',
                    completed_at = '2026-09-15 11:34:49.228000+00:00',
                    records_scraped = 0,
                    error_summary = 'Interrupted: host machine entered sleep state before database persistence transaction completed.'
                WHERE run_id = 328;
            """))
            session.commit()
            logger.info("Run 328 marked FAILED.")

        # ----------------------------------------------------------------------
        # STEP 3: DYNAMICALLY BUILD 125-TASK MATRIX
        # ----------------------------------------------------------------------
        routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
        windows = orchestrator.get_active_booking_windows()
        windows.sort(key=lambda w: w.display_order)

        tasks = orchestrator.build_task_matrix(routes, windows)
        logger.info("Built full 125-task matrix: %d routes × %d windows = %d tasks.",
                    len(routes), len(windows), len(tasks))
        assert len(tasks) == 125, f"Expected 125 tasks, got {len(tasks)}"

        # ----------------------------------------------------------------------
        # STEP 4: SEPARATE COMPLETED VS INCOMPLETE TASKS (DUPLICATE SAFETY)
        # ----------------------------------------------------------------------
        completed_tasks_map = {}  # task_index -> (task, run_info)
        incomplete_tasks = []      # list of (task_index, task)

        for idx, task in enumerate(tasks, start=1):
            existing = find_existing_successful_run(session, task)
            if existing:
                completed_tasks_map[idx] = (task, existing)
            else:
                incomplete_tasks.append((idx, task))

        logger.info("=== TASK RESUME BREAKDOWN ===")
        logger.info("Total Planned Tasks:        %d", len(tasks))
        logger.info("Already Completed Tasks:    %d (Will NOT rerun)", len(completed_tasks_map))
        logger.info("Incomplete Tasks to Run:    %d (Eligible for execution)", len(incomplete_tasks))
        assert len(completed_tasks_map) == 22, f"Expected 22 completed tasks, found {len(completed_tasks_map)}"
        assert len(incomplete_tasks) == 103, f"Expected 103 incomplete tasks, found {len(incomplete_tasks)}"

        # ----------------------------------------------------------------------
        # STEP 5: EXECUTE INCOMPLETE TASKS SEQUENTIALLY
        # ----------------------------------------------------------------------
        logger.info("\nStarting sequential execution of %d incomplete tasks on CLEARTRIP (delay=3.0s, single shared browser)...", len(incomplete_tasks))
        start_time_resume = time.time()

        executed_results = {}  # task_index -> TaskExecutionResult

        for current_num, (task_idx, task) in enumerate(incomplete_tasks, start=1):
            logger.info("--- [%d/%d] Executing Task %d: %s %s (travel_date=%s) ---",
                        current_num, len(incomplete_tasks), task_idx,
                        task.route.route_code, task.window.window_code, task.travel_date)

            task_result = orchestrator.execute_single_task(
                task=task,
                source_code="CLEARTRIP",
                browser_manager=browser_manager,
            )
            executed_results[task_idx] = task_result

            logger.info("Result for Task %d [Run %s]: status=%s, attempts=%d, retried=%s, inserted=%d",
                        task_idx, task_result.run_id, task_result.status,
                        task_result.attempts, task_result.retried, task_result.inserted_count)

            # Polite pacing between tasks
            if current_num < len(incomplete_tasks):
                time.sleep(3.0)

        elapsed_resume = time.time() - start_time_resume
        logger.info("=== Completed execution of %d tasks in %.2f seconds (%.2f minutes) ===",
                    len(incomplete_tasks), elapsed_resume, elapsed_resume / 60.0)

        # ----------------------------------------------------------------------
        # STEP 6: ASSEMBLE ALL 125 FINAL RUNS & AUDIT
        # ----------------------------------------------------------------------
        all_final_run_ids = []
        final_task_status_map = {}  # task_index -> dict

        for idx, task in enumerate(tasks, start=1):
            if idx in completed_tasks_map:
                _, existing_run = completed_tasks_map[idx]
                r_id = existing_run["run_id"]
                all_final_run_ids.append(r_id)
                final_task_status_map[idx] = {
                    "task_idx": idx,
                    "route_code": task.route.route_code,
                    "window_code": task.window.window_code,
                    "travel_date": str(task.travel_date),
                    "run_id": r_id,
                    "was_resumed": False,
                    "retried": False,
                    "recovered": False,
                }
            else:
                res = executed_results[idx]
                r_id = res.run_id
                all_final_run_ids.append(r_id)
                final_task_status_map[idx] = {
                    "task_idx": idx,
                    "route_code": task.route.route_code,
                    "window_code": task.window.window_code,
                    "travel_date": str(task.travel_date),
                    "run_id": r_id,
                    "was_resumed": True,
                    "retried": res.retried,
                    "recovered": res.recovered_on_retry,
                }

        assert len(all_final_run_ids) == 125, f"Expected 125 final run IDs, got {len(all_final_run_ids)}"
        logger.info("Auditing all 125 final runs via CollectionAuditService...")
        audit_summary = audit_service.audit_batch(all_final_run_ids)
        records = audit_summary.records

        # Map by run_id
        audit_rec_by_run_id = {r.run_id: r for r in records}

        # ----------------------------------------------------------------------
        # STEP 7: DIRECT SQL AUDIT ON ALL 125 FINAL RUNS
        # ----------------------------------------------------------------------
        run_ids_csv = ",".join(str(r) for r in all_final_run_ids)

        sql_final_stats = text(f"""
            SELECT 
                COUNT(DISTINCT cr.run_id) AS total_runs,
                COUNT(DISTINCT cr.run_id) FILTER (WHERE cr.status = 'COMPLETED') AS completed_runs,
                COUNT(DISTINCT cr.run_id) FILTER (WHERE cr.status = 'FAILED') AS failed_runs,
                COUNT(DISTINCT cr.run_id) FILTER (WHERE cr.status = 'COMPLETED' AND cr.records_scraped = 0) AS zero_inv_runs,
                COALESCE(SUM(cr.records_scraped), 0) AS total_scraped,
                COUNT(fo.observation_id) AS total_persisted,
                COUNT(DISTINCT fo.fingerprint_hash) AS distinct_fingerprints,
                COUNT(fo.observation_id) - COUNT(DISTINCT fo.fingerprint_hash) AS intra_run_duplicates,
                COUNT(*) FILTER (WHERE fo.total_fare <= 0 OR fo.is_valid = FALSE) AS invalid_fares,
                COUNT(DISTINCT fo.route_id) AS distinct_routes,
                COUNT(DISTINCT fo.window_id) AS distinct_windows,
                COUNT(DISTINCT fo.data_source_id) AS distinct_sources
            FROM collection_runs cr
            LEFT JOIN fare_observations fo ON fo.run_id = cr.run_id
            WHERE cr.run_id IN ({run_ids_csv});
        """)
        final_stats = session.execute(sql_final_stats).mappings().one()

        # Task-level parity check
        sql_parity_violations = text(f"""
            SELECT 
                cr.run_id,
                cr.records_scraped,
                COUNT(fo.observation_id) AS obs_count
            FROM collection_runs cr
            LEFT JOIN fare_observations fo ON fo.run_id = cr.run_id
            WHERE cr.run_id IN ({run_ids_csv}) AND cr.status = 'COMPLETED'
            GROUP BY cr.run_id, cr.records_scraped
            HAVING cr.records_scraped != COUNT(fo.observation_id);
        """)
        parity_violations = session.execute(sql_parity_violations).mappings().all()

        # Foreign key & isolation checks
        sql_isolation = text(f"""
            SELECT COUNT(*) AS mismatch_count
            FROM fare_observations fo
            JOIN collection_runs cr ON fo.run_id = cr.run_id
            WHERE cr.run_id IN ({run_ids_csv})
              AND (
                  fo.route_id != cr.target_route_id
                  OR fo.window_id != cr.target_window_id
                  OR fo.data_source_id != cr.source_id
              );
        """)
        isolation_mismatches = session.execute(sql_isolation).scalar()

        # Date correctness: travel_date = observation_date + advance_days
        sql_date_check = text(f"""
            SELECT 
                bw.window_code,
                bw.advance_days,
                COUNT(fo.observation_id) AS total_obs,
                COUNT(fo.observation_id) FILTER (WHERE fo.travel_date = (fo.observed_at AT TIME ZONE 'Asia/Kolkata')::date + (bw.advance_days * INTERVAL '1 day')::interval) AS correct_date_count,
                COUNT(fo.observation_id) FILTER (WHERE fo.travel_date != (fo.observed_at AT TIME ZONE 'Asia/Kolkata')::date + (bw.advance_days * INTERVAL '1 day')::interval) AS incorrect_date_count
            FROM fare_observations fo
            JOIN booking_windows bw ON fo.window_id = bw.window_id
            WHERE fo.run_id IN ({run_ids_csv})
            GROUP BY bw.window_code, bw.advance_days
            ORDER BY bw.advance_days;
        """)
        date_check_rows = session.execute(sql_date_check).mappings().all()

        # Secondary airport safety check
        sql_airport_safety = text(f"""
            SELECT COUNT(*) AS cluster_leak_count
            FROM fare_observations fo
            JOIN routes r ON fo.route_id = r.route_id
            JOIN airports orig ON r.origin_airport_id = orig.airport_id
            JOIN airports dest ON r.destination_airport_id = dest.airport_id
            WHERE fo.run_id IN ({run_ids_csv})
              AND (fo.origin_airport_code != orig.iata_code OR fo.destination_airport_code != dest.iata_code);
        """)
        cluster_leak_count = session.execute(sql_airport_safety).scalar()

        # Intra-run duplicates
        sql_intra_run_dups = text(f"""
            SELECT fo.run_id, fo.fingerprint_hash, COUNT(*) AS dup_count
            FROM fare_observations fo
            WHERE fo.run_id IN ({run_ids_csv})
            GROUP BY fo.run_id, fo.fingerprint_hash
            HAVING COUNT(*) > 1;
        """)
        intra_dups = session.execute(sql_intra_run_dups).mappings().all()

        # ----------------------------------------------------------------------
        # STEP 8: BUILD ROUTE × WINDOW MATRIX TABLE
        # ----------------------------------------------------------------------
        matrix = {}
        for r in routes:
            matrix[r.route_code] = {w.window_code: None for w in windows}

        for idx, task_info in final_task_status_map.items():
            r_id = task_info["run_id"]
            audit_rec = audit_rec_by_run_id.get(r_id)
            r_code = task_info["route_code"]
            w_code = task_info["window_code"]

            if audit_rec:
                matrix[r_code][w_code] = {
                    "run_id": r_id,
                    "task_idx": idx,
                    "health_status": audit_rec.health_status.value,
                    "db_count": audit_rec.db_observation_count,
                    "scraped_count": audit_rec.records_scraped,
                    "avg_fare": audit_rec.avg_fare,
                    "min_fare": audit_rec.min_fare,
                    "max_fare": audit_rec.max_fare,
                    "duration": audit_rec.duration_seconds,
                    "retried": task_info["retried"],
                    "recovered": task_info["recovered"],
                    "error": audit_rec.error_summary,
                }
            else:
                matrix[r_code][w_code] = {
                    "run_id": r_id,
                    "task_idx": idx,
                    "health_status": "UNKNOWN",
                    "db_count": 0,
                    "scraped_count": 0,
                    "avg_fare": None,
                    "min_fare": None,
                    "max_fare": None,
                    "duration": 0,
                    "retried": False,
                    "recovered": False,
                    "error": "No audit record",
                }

        # ----------------------------------------------------------------------
        # STEP 9: COMPILE STATISTICS & REPORT
        # ----------------------------------------------------------------------
        failed_tasks_recovered = sum(1 for idx in range(1, 10) if executed_results.get(idx) and executed_results[idx].status == "COMPLETED")
        interrupted_recovered = 1 if (executed_results.get(32) and executed_results[32].status == "COMPLETED") else 0
        new_tasks_executed = len(incomplete_tasks) - 9 - 1  # 93

        total_retries = sum(1 for res in executed_results.values() if res.retried)
        retry_recovered = sum(1 for res in executed_results.values() if res.recovered_on_retry)

        # Durations
        all_durations = [r.duration_seconds for r in records if r.duration_seconds is not None]
        avg_dur = statistics.mean(all_durations) if all_durations else 0
        median_dur = statistics.median(all_durations) if all_durations else 0
        min_dur = min(all_durations) if all_durations else 0
        max_dur = max(all_durations) if all_durations else 0

        # Booking window summaries
        window_stats = {}
        for w in windows:
            w_code = w.window_code
            w_recs = [r for r in records if r.window_code == w_code]
            w_obs = sum(r.db_observation_count for r in w_recs)
            w_fares = [r.avg_fare for r in w_recs if r.avg_fare is not None]
            avg_w_fare = statistics.mean(w_fares) if w_fares else 0
            window_stats[w_code] = {
                "advance_days": w.advance_days,
                "total_tasks": len(w_recs),
                "total_obs": w_obs,
                "avg_fare": avg_w_fare,
            }

        # Route summaries
        route_stats = {}
        for r_code, win_map in matrix.items():
            route_obs = sum((cell["db_count"] if cell and cell["db_count"] else 0) for cell in win_map.values())
            route_stats[r_code] = route_obs

        # Print console tables
        print("\n" + "=" * 115)
        print("FINAL CLEARTRIP 125-TASK COLLECTION MATRIX REPORT")
        print("=" * 115 + "\n")

        print("SECTION 1: 25-ROUTE × 5-WINDOW COVERAGE MATRIX")
        print("-" * 115)
        header = f"{'Route':<10} | {'T+1':<18} | {'T+7':<18} | {'T+15':<18} | {'T+30':<18} | {'T+45':<18}"
        print(header)
        print("-" * 115)

        for r_code in sorted(matrix.keys()):
            cols = []
            for w in windows:
                cell = matrix[r_code].get(w.window_code)
                if not cell:
                    cols.append("NOT RUN")
                elif cell["health_status"] == "SUCCESS":
                    tag = f"SUCCESS ({cell['db_count']})"
                    if cell.get("recovered"):
                        tag += " [RETRY]"
                    cols.append(tag)
                elif cell["health_status"] == "NO_INVENTORY":
                    cols.append("ZERO INVENTORY")
                elif cell["health_status"] == "LOW_DATA":
                    cols.append(f"LOW DATA ({cell['db_count']})")
                else:
                    err_msg = (cell.get("error") or "FAILED")[:12]
                    cols.append(f"FAILED ({err_msg})")
            print(f"{r_code:<10} | {cols[0]:<18} | {cols[1]:<18} | {cols[2]:<18} | {cols[3]:<18} | {cols[4]:<18}")

        print("-" * 115)

        print("\nSECTION 2: SUMMARY METRICS")
        print("-" * 115)
        print(f"1.  Total expected tasks:             125")
        print(f"2.  Tasks already completed:          22")
        print(f"3.  Failed tasks recovered:           {failed_tasks_recovered}/9")
        print(f"4.  Interrupted task recovered:       {interrupted_recovered}/1 (Run 328 -> {executed_results.get(32).run_id if executed_results.get(32) else 'N/A'})")
        print(f"5.  New tasks executed:               {new_tasks_executed}")
        print(f"6.  Total successful tasks:           {audit_summary.success_count + audit_summary.no_inventory_count}/125 ({((audit_summary.success_count + audit_summary.no_inventory_count)/125*100):.1f}%)")
        print(f"7.  Total technical failures:         {audit_summary.failed_count}/125")
        print(f"8.  Zero-inventory tasks:             {audit_summary.no_inventory_count}")
        print(f"9.  Total observations scraped:       {final_stats['total_scraped']}")
        print(f"10. Total observations persisted:     {final_stats['total_persisted']}")
        print(f"11. Persistence parity:               { '100% PARITY (scraped == persisted)' if len(parity_violations) == 0 and final_stats['total_scraped'] == final_stats['total_persisted'] else f'{len(parity_violations)} mismatches' }")
        print(f"12. Distinct fingerprints:            {final_stats['distinct_fingerprints']}")
        print(f"13. Intra-run duplicate count:        {len(intra_dups)}")
        print(f"14. Distinct routes covered:          {final_stats['distinct_routes']}/25")
        print(f"15. Distinct booking windows:         {final_stats['distinct_windows']}/5")
        print(f"16. Incomplete tasks runtime:         {elapsed_resume:.2f}s ({elapsed_resume / 60.0:.2f} minutes)")
        print(f"    Avg / Median task duration:       {avg_dur:.2f}s / {median_dur:.2f}s (Min: {min_dur:.2f}s, Max: {max_dur:.2f}s)")
        print(f"17. Retry statistics:                 {total_retries} tasks retried, {retry_recovered} recovered on retry")
        print(f"18. Remaining failures:               {audit_summary.failed_count}")

        print("\nSECTION 3: BOOKING-WINDOW COVERAGE & ADVANCE DAYS AUDIT")
        print("-" * 115)
        for d_row in date_check_rows:
            w_code = d_row["window_code"]
            info = window_stats.get(w_code, {})
            print(
                f"Window {w_code:<5} (Advance={d_row['advance_days']:>2}d): "
                f"{d_row['total_obs']:>5} obs | "
                f"Avg Fare: INR {info.get('avg_fare', 0):>8.2f} | "
                f"Date Match: {d_row['correct_date_count']}/{d_row['total_obs']} "
                f"({'100% PASS' if d_row['incorrect_date_count'] == 0 else 'FAIL'})"
            )

        print("\nSECTION 4: PERSISTENCE & DATA QUALITY AUDIT")
        print("-" * 115)
        print(f"Foreign Key / Isolation Mismatches:   {isolation_mismatches}")
        print(f"Secondary Airport Leakages:           {cluster_leak_count}")
        print(f"Invalid Fares (<= 0):                 {final_stats['invalid_fares']}")
        print(f"Intra-run Duplicate Fingerprints:     {len(intra_dups)}")

        # Save structured results to JSON
        output_path = Path("scratch/matrix_125_final_results.json")
        result_export = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_expected_tasks": 125,
            "tasks_already_completed": 22,
            "failed_tasks_recovered": failed_tasks_recovered,
            "interrupted_task_recovered": interrupted_recovered,
            "new_tasks_executed": new_tasks_executed,
            "total_successful_tasks": audit_summary.success_count + audit_summary.no_inventory_count,
            "total_technical_failures": audit_summary.failed_count,
            "zero_inventory_tasks": audit_summary.no_inventory_count,
            "total_scraped": int(final_stats["total_scraped"]),
            "total_persisted": int(final_stats["total_persisted"]),
            "persistence_parity_violations": len(parity_violations),
            "distinct_fingerprints": int(final_stats["distinct_fingerprints"]),
            "intra_run_duplicates": len(intra_dups),
            "invalid_fares": int(final_stats["invalid_fares"]),
            "isolation_mismatches": int(isolation_mismatches),
            "cluster_leak_count": int(cluster_leak_count),
            "total_resume_runtime_seconds": elapsed_resume,
            "avg_task_duration": avg_dur,
            "median_task_duration": median_dur,
            "min_task_duration": min_dur,
            "max_task_duration": max_dur,
            "retried_tasks": total_retries,
            "retry_recovered": retry_recovered,
            "windows": window_stats,
            "route_observations": route_stats,
            "matrix": matrix,
            "final_run_ids": all_final_run_ids,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result_export, f, indent=2)
        logger.info("Saved final 125-matrix results to %s", output_path)

    finally:
        browser_manager.close()
        session.close()


if __name__ == "__main__":
    main()
