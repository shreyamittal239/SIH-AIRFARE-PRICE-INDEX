"""Execute a clean live fare collection for the 25-route × 5-window master route basket.

Source: Cleartrip OTA only
Master Basket: 25 active DGCA routes dynamically loaded from route_weights
Booking Windows: 5 active windows (T+1, T+7, T+15, T+30, T+45) dynamically loaded from booking_windows
Total Tasks: 125 tasks
Pacing: 3.0s sequential delay, single shared Chromium browser context
Technical Retry: Exactly 1 retry after 5.0s backoff for transient technical errors only (no retry on zero inventory)
Persistence: Full PostgreSQL transactional ingestion and CollectionAudit
"""

from datetime import datetime, timezone
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
from backend.collectors.orchestrator import CollectionOrchestrator
from backend.collectors.playwright.browser import BrowserManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("clean_master_collection")


def main() -> None:
    session = SessionLocal()
    browser_manager = BrowserManager(headless=True)
    try:
        orchestrator = CollectionOrchestrator(db=session)
        audit_service = CollectionAuditService(db=session)

        # ----------------------------------------------------------------------
        # STEP 1: INSPECT CURRENT MASTER ROUTES & WINDOWS
        # ----------------------------------------------------------------------
        routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
        windows = orchestrator.get_active_booking_windows()
        windows.sort(key=lambda w: w.display_order)

        logger.info("=== STEP 1: CURRENT MASTER CONFIGURATION INSPECTION ===")
        logger.info("Retrieved %d active routes from route_weights.", len(routes))
        logger.info("Retrieved %d active booking windows: %s", len(windows), [w.window_code for w in windows])

        if len(routes) != 25 or [w.window_code for w in windows] != ["T+1", "T+7", "T+15", "T+30", "T+45"]:
            logger.error("Configuration mismatch! Expected 25 routes and ['T+1', 'T+7', 'T+15', 'T+30', 'T+45']. Aborting.")
            sys.exit(1)

        # Record baseline maximum run_id before starting new collection cycle
        baseline_max_run_id = session.execute(text("SELECT COALESCE(MAX(run_id), 0) FROM collection_runs")).scalar()
        logger.info("Baseline max run_id in database: %d (all new runs will have run_id > %d)", baseline_max_run_id, baseline_max_run_id)

        # ----------------------------------------------------------------------
        # STEP 2: BUILD COLLECTION MATRIX
        # ----------------------------------------------------------------------
        tasks = orchestrator.build_task_matrix(routes, windows)
        total_tasks = len(tasks)
        logger.info("=== STEP 2: COLLECTION MATRIX GENERATED ===")
        logger.info("Constructed %d tasks (%d routes × %d windows).", total_tasks, len(routes), len(windows))
        assert total_tasks == 125, f"Expected 125 tasks, got {total_tasks}"

        # ----------------------------------------------------------------------
        # STEP 3 & 4: EXECUTE NEW COLLECTION CYCLE SEQUENTIALLY
        # ----------------------------------------------------------------------
        source_code = "CLEARTRIP"
        logger.info("=== STEP 3 & 4: STARTING LIVE COLLECTION CYCLE ON CLEARTRIP ===")
        logger.info("Sequential execution (delay=3.0s, single shared browser context, 1 retry with 5s backoff)...")
        start_time_all = time.time()

        batch_summary = orchestrator.run_collection(
            source_code=source_code,
            tasks=tasks,
            delay_seconds=3.0,
            browser_manager=browser_manager,
        )

        total_elapsed = time.time() - start_time_all
        logger.info(
            "=== 125-Task Batch Collection Completed in %.2f seconds (%.2f minutes) ===",
            total_elapsed,
            total_elapsed / 60.0,
        )

        # ----------------------------------------------------------------------
        # STEP 5 & 6: GATHER NEW RUN IDS & AUDIT
        # ----------------------------------------------------------------------
        new_run_ids = [res.run_id for res in batch_summary.results if res.run_id is not None and res.run_id > baseline_max_run_id]
        logger.info("Collected %d new Run IDs from batch execution (run_id > %d).", len(new_run_ids), baseline_max_run_id)
        assert len(new_run_ids) == 125, f"Expected 125 new run IDs, got {len(new_run_ids)}"

        logger.info("Auditing all %d runs in PostgreSQL via CollectionAuditService...", len(new_run_ids))
        audit_summary = audit_service.audit_batch(new_run_ids)
        records = audit_summary.records

        # Map run_id to TaskExecutionResult for retry tracking
        task_res_map = {res.run_id: res for res in batch_summary.results if res.run_id is not None}
        run_ids_csv = ",".join(str(r) for r in new_run_ids)

        # ----------------------------------------------------------------------
        # STEP 7 & 10: DIRECT SQL VERIFICATION CHECKS
        # ----------------------------------------------------------------------
        # 1. Total runs and counts
        sql_run_counts = text(f"""
            SELECT 
                COUNT(DISTINCT cr.run_id) AS total_runs,
                COUNT(DISTINCT cr.run_id) FILTER (WHERE cr.status = 'COMPLETED') AS completed_runs,
                COUNT(DISTINCT cr.run_id) FILTER (WHERE cr.status = 'FAILED') AS failed_runs,
                COUNT(DISTINCT cr.run_id) FILTER (WHERE cr.status = 'COMPLETED' AND cr.records_scraped = 0) AS zero_inv_runs,
                COUNT(DISTINCT cr.run_id) FILTER (WHERE cr.status = 'COMPLETED' AND cr.records_scraped > 0) AS positive_quote_runs,
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
        final_stats = session.execute(sql_run_counts).mappings().one()

        # 2. Scraped vs persisted parity per task
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

        # 3. Foreign key & isolation checks
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

        # 4. Date correctness: travel_date = observation_date + advance_days
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

        # 5. Airport safety check (secondary airport isolation)
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

        # 6. Intra-run duplicate check
        sql_intra_run_dups = text(f"""
            SELECT fo.run_id, fo.fingerprint_hash, COUNT(*) AS dup_count
            FROM fare_observations fo
            WHERE fo.run_id IN ({run_ids_csv})
            GROUP BY fo.run_id, fo.fingerprint_hash
            HAVING COUNT(*) > 1;
        """)
        intra_dups = session.execute(sql_intra_run_dups).mappings().all()

        # 7. Verification that historical runs were NOT modified
        sql_hist_modified = text(f"""
            SELECT COUNT(*) AS modified_hist_runs
            FROM collection_runs
            WHERE run_id <= :baseline_max
              AND completed_at > :start_time;
        """)
        hist_modified = session.execute(sql_hist_modified, {
            "baseline_max": baseline_max_run_id,
            "start_time": datetime.fromtimestamp(start_time_all, tz=timezone.utc),
        }).scalar()

        # ----------------------------------------------------------------------
        # STEP 8: BUILD ROUTE × WINDOW MATRIX
        # ----------------------------------------------------------------------
        matrix = {}
        for r in routes:
            matrix[r.route_code] = {w.window_code: None for w in windows}

        for rec in records:
            res_meta = task_res_map.get(rec.run_id)
            matrix[rec.route_code][rec.window_code] = {
                "run_id": rec.run_id,
                "health_status": rec.health_status.value,
                "db_count": rec.db_observation_count,
                "scraped_count": rec.records_scraped,
                "avg_fare": rec.avg_fare,
                "min_fare": rec.min_fare,
                "max_fare": rec.max_fare,
                "duration": rec.duration_seconds,
                "retried": res_meta.retried if res_meta else False,
                "recovered": res_meta.recovered_on_retry if res_meta else False,
                "error": rec.error_summary,
            }

        # ----------------------------------------------------------------------
        # STEP 9: COMPUTE ALL 23 SUMMARY METRICS
        # ----------------------------------------------------------------------
        # Durations
        durations = [r.duration_seconds for r in records if r.duration_seconds is not None]
        avg_dur = statistics.mean(durations) if durations else 0
        median_dur = statistics.median(durations) if durations else 0
        min_dur = min(durations) if durations else 0
        max_dur = max(durations) if durations else 0

        # Retries
        tasks_retried = sum(1 for res in batch_summary.results if res.retried)
        tasks_recovered = sum(1 for res in batch_summary.results if res.recovered_on_retry)
        tasks_still_failed_after_retry = sum(1 for res in batch_summary.results if res.retried and res.status == "FAILED")

        # Booking window summaries
        window_stats = {}
        for w in windows:
            w_code = w.window_code
            w_recs = [r for r in records if r.window_code == w_code]
            w_obs = sum(r.db_observation_count for r in w_recs)
            w_fares = [r.avg_fare for r in w_recs if r.avg_fare is not None]
            avg_w_fare = statistics.mean(w_fares) if w_fares else 0
            window_stats[w_code] = {
                "advance_days": w.target_advance_days,
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
        print("CLEAN MASTER LIVE COLLECTION REPORT (25 ROUTES × 5 WINDOWS)")
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

        print("\nSECTION 2: ALL 23 SUMMARY METRICS")
        print("-" * 115)
        print(f" 1. Current active route count:              {len(routes)}")
        print(f" 2. Current active booking-window count:      {len(windows)}")
        print(f" 3. Expected task count:                     {total_tasks}")
        print(f" 4. Tasks executed:                          {len(records)}")
        print(f" 5. Successful tasks with quotes:            {final_stats['positive_quote_runs']}")
        print(f" 6. Confirmed zero-inventory tasks:          {final_stats['zero_inv_runs']}")
        print(f" 7. Technical failures:                      {final_stats['failed_runs']}")
        print(f" 8. Tasks that required retry:               {tasks_retried}")
        print(f" 9. Tasks still failed after retry:          {tasks_still_failed_after_retry}")
        print(f"10. Total observations scraped:              {final_stats['total_scraped']}")
        print(f"11. Total observations persisted:            {final_stats['total_persisted']}")
        print(f"12. Persistence parity:                      {'100% PARITY' if len(parity_violations) == 0 and final_stats['total_scraped'] == final_stats['total_persisted'] else f'{len(parity_violations)} mismatches'}")
        print(f"13. Distinct fingerprints:                   {final_stats['distinct_fingerprints']}")
        print(f"14. Duplicate observations:                  {len(intra_dups)}")
        print(f"15. Distinct routes with observations:       {final_stats['distinct_routes']}")
        for w_code in ["T+1", "T+7", "T+15", "T+30", "T+45"]:
            w_info = window_stats.get(w_code, {})
            num = 16 + ["T+1", "T+7", "T+15", "T+30", "T+45"].index(w_code)
            print(f"{num:>2}. {w_code} observation/task coverage:           {w_info.get('total_obs', 0)} obs across {w_info.get('total_tasks', 0)} tasks (avg INR {w_info.get('avg_fare', 0):.2f})")
        print(f"21. Total runtime:                           {total_elapsed:.2f}s ({total_elapsed / 60.0:.2f} minutes)")
        print(f"22. Average task duration:                   {avg_dur:.2f}s (median: {median_dur:.2f}s)")
        print(f"23. Minimum / maximum task duration:         {min_dur:.2f}s / {max_dur:.2f}s")

        print("\nSECTION 3: DATABASE INTEGRITY CHECKS")
        print("-" * 115)
        print(f"Foreign Key / Isolation Mismatches:          {isolation_mismatches}")
        print(f"Secondary Airport Leakages:                  {cluster_leak_count}")
        print(f"Invalid Fares (<= 0):                        {final_stats['invalid_fares']}")
        print(f"Historical Runs Modified (run_id <= {baseline_max_run_id}): {hist_modified}")

        # Save structured results to JSON
        output_path = Path("scratch/clean_collection_cycle_results.json")
        result_export = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "baseline_max_run_id": baseline_max_run_id,
            "active_routes_count": len(routes),
            "active_windows_count": len(windows),
            "expected_task_count": total_tasks,
            "tasks_executed": len(records),
            "successful_tasks_with_quotes": int(final_stats["positive_quote_runs"]),
            "zero_inventory_tasks": int(final_stats["zero_inv_runs"]),
            "technical_failures": int(final_stats["failed_runs"]),
            "tasks_retried": tasks_retried,
            "tasks_recovered": tasks_recovered,
            "tasks_still_failed_after_retry": tasks_still_failed_after_retry,
            "total_scraped": int(final_stats["total_scraped"]),
            "total_persisted": int(final_stats["total_persisted"]),
            "persistence_parity_violations": len(parity_violations),
            "distinct_fingerprints": int(final_stats["distinct_fingerprints"]),
            "intra_run_duplicates": len(intra_dups),
            "distinct_routes_with_obs": int(final_stats["distinct_routes"]),
            "distinct_windows_with_obs": int(final_stats["distinct_windows"]),
            "isolation_mismatches": int(isolation_mismatches),
            "cluster_leak_count": int(cluster_leak_count),
            "invalid_fares": int(final_stats["invalid_fares"]),
            "historical_runs_modified": int(hist_modified),
            "total_runtime_seconds": total_elapsed,
            "avg_task_duration": avg_dur,
            "median_task_duration": median_dur,
            "min_task_duration": min_dur,
            "max_task_duration": max_dur,
            "windows": window_stats,
            "route_observations": route_stats,
            "matrix": matrix,
            "run_ids": new_run_ids,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result_export, f, indent=2)
        logger.info("Saved clean collection cycle results to %s", output_path)

    finally:
        browser_manager.close()
        session.close()


if __name__ == "__main__":
    main()
