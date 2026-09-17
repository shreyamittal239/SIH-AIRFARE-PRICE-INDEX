"""Execute the complete 125-task Cleartrip collection matrix.

25 active DGCA routes × 5 active booking windows (T+1, T+7, T+15, T+30, T+45) × CLEARTRIP only.
Total tasks = 125.
Performs complete persistence, isolation, date correctness, and fingerprint audit against PostgreSQL.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import statistics
import sys
import time

# Ensure stdout handles utf-8 safely on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from backend.app.db.database import SessionLocal
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
logger = logging.getLogger("cleartrip_125_matrix")


def main() -> None:
    session = SessionLocal()
    browser_manager = BrowserManager(headless=True)
    try:
        orchestrator = CollectionOrchestrator(db=session)
        audit_service = CollectionAuditService(db=session)

        # ----------------------------------------------------------------------
        # STEP 1: DYNAMIC LOAD OF ROUTES AND WINDOWS
        # ----------------------------------------------------------------------
        active_routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
        logger.info("Loaded %d active routes from route_weights.", len(active_routes))
        if len(active_routes) != 25:
            logger.warning("Expected 25 active routes, found %d", len(active_routes))

        all_windows = orchestrator.get_active_booking_windows()
        # Sort by display_order
        all_windows.sort(key=lambda w: w.display_order)
        logger.info("Loaded %d active booking windows: %s", len(all_windows), [w.window_code for w in all_windows])

        # ----------------------------------------------------------------------
        # STEP 2: BUILD 125-TASK MATRIX
        # ----------------------------------------------------------------------
        tasks = orchestrator.build_task_matrix(active_routes, all_windows)
        total_expected_tasks = len(tasks)
        logger.info(
            "Constructed %d collection tasks (%d routes × %d windows).",
            total_expected_tasks,
            len(active_routes),
            len(all_windows),
        )
        assert total_expected_tasks == 125, f"Expected 125 tasks, got {total_expected_tasks}"

        # ----------------------------------------------------------------------
        # STEP 3: RUN 125-TASK BATCH SEQUENTIALLY ON CLEARTRIP
        # ----------------------------------------------------------------------
        source_code = "CLEARTRIP"
        logger.info("Starting sequential execution on %s (delay=3.0s, single shared browser)...", source_code)
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

        run_ids = [res.run_id for res in batch_summary.results if res.run_id is not None]
        logger.info("Collected %d Run IDs from batch execution.", len(run_ids))

        # ----------------------------------------------------------------------
        # STEP 4: AUDIT ALL RUNS USING COLLECTION AUDIT SERVICE
        # ----------------------------------------------------------------------
        logger.info("Auditing %d collection runs in PostgreSQL...", len(run_ids))
        audit_summary = audit_service.audit_batch(run_ids)
        records = audit_summary.records

        # Map run_id to TaskExecutionResult for retry tracking
        task_res_map = {res.run_id: res for res in batch_summary.results if res.run_id is not None}

        # ----------------------------------------------------------------------
        # STEP 5: SQL DIRECT VERIFICATIONS ON DATABASE
        # ----------------------------------------------------------------------
        run_ids_str = ",".join(str(r) for r in run_ids)

        # 1. Total runs, successful, failed, zero-inventory
        sql_run_counts = text(f"""
            SELECT 
                count(*) AS total_runs,
                count(*) FILTER (WHERE status = 'COMPLETED') AS completed_runs,
                count(*) FILTER (WHERE status = 'FAILED') AS failed_runs,
                count(*) FILTER (WHERE status = 'COMPLETED' AND records_scraped = 0) AS zero_inv_runs,
                count(*) FILTER (WHERE status = 'COMPLETED' AND records_scraped > 0) AS positive_quote_runs
            FROM collection_runs
            WHERE run_id IN ({run_ids_str});
        """)
        run_counts = session.execute(sql_run_counts).mappings().one()

        # 2. Scraped quotes vs Persisted observations
        sql_parity = text(f"""
            SELECT 
                COALESCE(SUM(cr.records_scraped), 0) AS total_scraped,
                COALESCE(SUM(cr.records_inserted), 0) AS total_run_inserted,
                COUNT(fo.observation_id) AS total_db_observations,
                COUNT(DISTINCT fo.fingerprint_hash) AS distinct_fingerprints,
                COUNT(fo.observation_id) - COUNT(DISTINCT fo.fingerprint_hash) AS intra_run_duplicates,
                COUNT(*) FILTER (WHERE fo.total_fare <= 0 OR fo.is_valid = FALSE) AS invalid_fares,
                COUNT(DISTINCT fo.route_id) AS distinct_routes,
                COUNT(DISTINCT fo.window_id) AS distinct_windows,
                COUNT(DISTINCT fo.source_id) AS distinct_sources
            FROM collection_runs cr
            LEFT JOIN fare_observations fo ON fo.run_id = cr.run_id
            WHERE cr.run_id IN ({run_ids_str});
        """)
        parity_stats = session.execute(sql_parity).mappings().one()

        # 3. Task-level parity check: scraped == persisted for every successful task
        sql_task_parity = text(f"""
            SELECT 
                cr.run_id,
                cr.route_id,
                cr.window_id,
                cr.records_scraped,
                cr.records_inserted,
                COUNT(fo.observation_id) AS obs_count
            FROM collection_runs cr
            LEFT JOIN fare_observations fo ON fo.run_id = cr.run_id
            WHERE cr.run_id IN ({run_ids_str}) AND cr.status = 'COMPLETED'
            GROUP BY cr.run_id, cr.route_id, cr.window_id, cr.records_scraped, cr.records_inserted
            HAVING cr.records_inserted != COUNT(fo.observation_id);
        """)
        task_parity_violations = session.execute(sql_task_parity).mappings().all()

        # 4. Foreign key and isolation verification
        # Every persisted observation must match the run's route_id, window_id, travel_date, source_id
        sql_isolation = text(f"""
            SELECT COUNT(*) AS mismatch_count
            FROM fare_observations fo
            JOIN collection_runs cr ON fo.run_id = cr.run_id
            JOIN booking_windows bw ON fo.window_id = bw.window_id
            WHERE cr.run_id IN ({run_ids_str})
              AND (
                  fo.route_id != cr.route_id
                  OR fo.window_id != cr.window_id
                  OR fo.source_id != cr.source_id
                  OR fo.travel_date != cr.travel_date
              );
        """)
        isolation_mismatches = session.execute(sql_isolation).scalar()

        # 5. Date correctness check: travel_date = observation_date + advance_days
        sql_date_correctness = text(f"""
            SELECT 
                bw.window_code,
                bw.advance_days AS target_advance,
                COUNT(*) AS total_obs,
                COUNT(*) FILTER (WHERE fo.travel_date = cr.observation_date + (bw.advance_days * INTERVAL '1 day')::interval) AS correct_date_count,
                COUNT(*) FILTER (WHERE fo.travel_date != cr.observation_date + (bw.advance_days * INTERVAL '1 day')::interval) AS incorrect_date_count
            FROM fare_observations fo
            JOIN collection_runs cr ON fo.run_id = cr.run_id
            JOIN booking_windows bw ON fo.window_id = bw.window_id
            WHERE cr.run_id IN ({run_ids_str})
            GROUP BY bw.window_code, bw.advance_days
            ORDER BY bw.advance_days;
        """)
        date_correctness_rows = session.execute(sql_date_correctness).mappings().all()

        # 6. Fingerprint uniqueness within each run
        sql_intra_run_dups = text(f"""
            SELECT fo.run_id, fo.fingerprint_hash, COUNT(*) AS dup_count
            FROM fare_observations fo
            WHERE fo.run_id IN ({run_ids_str})
            GROUP BY fo.run_id, fo.fingerprint_hash
            HAVING COUNT(*) > 1;
        """)
        intra_run_dups = session.execute(sql_intra_run_dups).mappings().all()

        # 7. Secondary airport isolation check
        sql_airport_safety = text(f"""
            SELECT COUNT(*) AS cluster_leak_count
            FROM fare_observations fo
            JOIN routes r ON fo.route_id = r.route_id
            JOIN airports orig ON r.origin_airport_id = orig.airport_id
            JOIN airports dest ON r.destination_airport_id = dest.airport_id
            WHERE fo.run_id IN ({run_ids_str})
              AND (fo.origin_code != orig.iata_code OR fo.destination_code != dest.iata_code);
        """)
        cluster_leak_count = session.execute(sql_airport_safety).scalar()

        # ----------------------------------------------------------------------
        # STEP 6: PRODUCE ROUTE × WINDOW COVERAGE MATRIX
        # ----------------------------------------------------------------------
        # Matrix cells: Route -> Window -> {status, obs_count, error, retried, recovered}
        matrix = {}
        for r in active_routes:
            matrix[r.route_code] = {w.window_code: None for w in all_windows}

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
        # STEP 7: COMPUTE SUMMARY STATISTICS
        # ----------------------------------------------------------------------
        durations = [r.duration_seconds for r in records if r.duration_seconds is not None]
        avg_dur = statistics.mean(durations) if durations else 0
        median_dur = statistics.median(durations) if durations else 0
        min_dur = min(durations) if durations else 0
        max_dur = max(durations) if durations else 0

        total_retries = sum(1 for res in batch_summary.results if res.retried)
        retry_recovered = sum(1 for res in batch_summary.results if res.recovered_on_retry)

        # Observation stats per task
        positive_tasks = [r.db_observation_count for r in records if r.db_observation_count > 0]
        avg_obs_per_task = statistics.mean(positive_tasks) if positive_tasks else 0
        min_obs_per_task = min(positive_tasks) if positive_tasks else 0
        max_obs_per_task = max(positive_tasks) if positive_tasks else 0

        # Observations per route
        route_obs_totals = {}
        for r_code, win_map in matrix.items():
            route_obs_totals[r_code] = sum(
                (cell["db_count"] if cell and cell["db_count"] else 0) for cell in win_map.values()
            )

        # Booking-window coverage
        window_summary = {}
        for w in all_windows:
            w_code = w.window_code
            w_records = [r for r in records if r.window_code == w_code]
            w_obs = sum(r.db_observation_count for r in w_records)
            w_fares = [r.avg_fare for r in w_records if r.avg_fare is not None]
            avg_w_fare = statistics.mean(w_fares) if w_fares else 0
            window_summary[w_code] = {
                "advance_days": w.advance_days,
                "total_tasks": len(w_records),
                "total_observations": w_obs,
                "avg_fare": avg_w_fare,
            }

        # ----------------------------------------------------------------------
        # PRINT CONSOLE REPORT
        # ----------------------------------------------------------------------
        print("\n" + "=" * 105)
        print("CLEARTRIP 125-TASK COLLECTION MATRIX REPORT")
        print("=" * 105 + "\n")

        print("SECTION A: 25-ROUTE × 5-WINDOW COVERAGE MATRIX")
        print("-" * 105)
        header = f"{'Route':<10} | {'T+1':<17} | {'T+7':<17} | {'T+15':<17} | {'T+30':<17} | {'T+45':<17}"
        print(header)
        print("-" * 105)

        for r_code in sorted(matrix.keys()):
            cols = []
            for w in all_windows:
                cell = matrix[r_code].get(w.window_code)
                if not cell:
                    cols.append("NOT RUN")
                elif cell["health_status"] == "SUCCESS":
                    tag = f"SUCCESS ({cell['db_count']})"
                    if cell["recovered"]:
                        tag += " [RETRY]"
                    cols.append(tag)
                elif cell["health_status"] == "NO_INVENTORY":
                    cols.append("ZERO INVENTORY")
                elif cell["health_status"] == "LOW_DATA":
                    cols.append(f"LOW DATA ({cell['db_count']})")
                else:
                    err_msg = (cell["error"] or "FAILED")[:12]
                    cols.append(f"FAILED ({err_msg})")
            print(f"{r_code:<10} | {cols[0]:<17} | {cols[1]:<17} | {cols[2]:<17} | {cols[3]:<17} | {cols[4]:<17}")

        print("-" * 105)

        print("\nSECTION B: SUMMARY STATISTICS")
        print("-" * 105)
        print(f"Total Expected Tasks:         {total_expected_tasks}")
        print(f"Total Actual Tasks:           {len(records)}")
        print(f"Successful Tasks:             {audit_summary.success_count + audit_summary.no_inventory_count}")
        print(f"  - Positive Quotes Tasks:    {audit_summary.success_count}")
        print(f"  - Zero Inventory Tasks:     {audit_summary.no_inventory_count}")
        print(f"Failed Tasks:                 {audit_summary.failed_count}")
        print(f"Tasks Attempted Retry:        {total_retries}")
        print(f"Tasks Recovered on Retry:     {retry_recovered}")
        print(f"Task Success Rate:            {((audit_summary.success_count + audit_summary.no_inventory_count) / total_expected_tasks * 100):.1f}%")
        print(f"Total Failure Rate:           {audit_summary.failure_rate:.1f}%")
        print(f"Retry Recovery Rate:          {(retry_recovered / total_retries * 100) if total_retries else 100.0:.1f}%\n")

        print(f"Total Scraped Quotes:         {parity_stats['total_scraped']}")
        print(f"Total Persisted Observations: {parity_stats['total_db_observations']}")
        print(f"Persistence Parity Violations:{len(task_parity_violations)}")
        print(f"Distinct Fingerprints:        {parity_stats['distinct_fingerprints']}")
        print(f"Intra-run Duplicates:         {parity_stats['intra_run_duplicates']}")
        print(f"Invalid Fares (<= 0):         {parity_stats['invalid_fares']}")
        print(f"Distinct Routes in DB:        {parity_stats['distinct_routes']}")
        print(f"Distinct Windows in DB:       {parity_stats['distinct_windows']}")
        print(f"Distinct Sources in DB:       {parity_stats['distinct_sources']}\n")

        print(f"Total Matrix Runtime:         {total_elapsed:.2f}s ({total_elapsed / 60.0:.2f} minutes)")
        print(f"Average Task Duration:        {avg_dur:.2f}s")
        print(f"Median Task Duration:         {median_dur:.2f}s")
        print(f"Min Task Duration:            {min_dur:.2f}s")
        print(f"Max Task Duration:            {max_dur:.2f}s\n")

        print("SECTION C: BOOKING-WINDOW COVERAGE & ADVANCE DAYS AUDIT")
        print("-" * 105)
        for d_row in date_correctness_rows:
            w_code = d_row["window_code"]
            info = window_summary.get(w_code, {})
            print(
                f"Window {w_code:<5} (Advance={d_row['target_advance']:>2}d): "
                f"{d_row['total_obs']:>5} obs | "
                f"Avg Fare: INR {info.get('avg_fare', 0):>8.2f} | "
                f"Date Match: {d_row['correct_date_count']}/{d_row['total_obs']} "
                f"({ '100% PASS' if d_row['incorrect_date_count'] == 0 else 'FAIL' })"
            )

        print("\nSECTION D: PERSISTENCE & SAFETY AUDIT")
        print("-" * 105)
        print(f"Foreign Key / Isolation Mismatches: {isolation_mismatches}")
        print(f"Secondary Airport Leakages:         {cluster_leak_count}")
        print(f"Intra-Run Duplicate Fingerprints:   {len(intra_run_dups)}")

        # Save structured results to JSON
        output_path = Path("scratch/matrix_125_results.json")
        result_export = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_expected_tasks": total_expected_tasks,
            "total_actual_tasks": len(records),
            "successful_tasks": audit_summary.success_count + audit_summary.no_inventory_count,
            "positive_tasks": audit_summary.success_count,
            "zero_inventory_tasks": audit_summary.no_inventory_count,
            "failed_tasks": audit_summary.failed_count,
            "retried_tasks": total_retries,
            "retry_recovered_tasks": retry_recovered,
            "total_scraped": int(parity_stats["total_scraped"]),
            "total_persisted": int(parity_stats["total_db_observations"]),
            "persistence_parity_violations": len(task_parity_violations),
            "distinct_fingerprints": int(parity_stats["distinct_fingerprints"]),
            "intra_run_duplicates": int(parity_stats["intra_run_duplicates"]),
            "invalid_fares": int(parity_stats["invalid_fares"]),
            "isolation_mismatches": int(isolation_mismatches),
            "cluster_leak_count": int(cluster_leak_count),
            "total_runtime_seconds": total_elapsed,
            "avg_task_duration": avg_dur,
            "median_task_duration": median_dur,
            "min_task_duration": min_dur,
            "max_task_duration": max_dur,
            "windows": window_summary,
            "route_observations": route_obs_totals,
            "matrix": matrix,
            "run_ids": run_ids,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result_export, f, indent=2)
        logger.info("Saved 125-matrix results to %s", output_path)

    finally:
        browser_manager.close()
        session.close()


if __name__ == "__main__":
    main()
