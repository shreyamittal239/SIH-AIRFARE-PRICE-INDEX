"""Read-only check of PostgreSQL state for the 125-task run from run_id >= 297."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from backend.app.db.database import SessionLocal

session = SessionLocal()
try:
    # 1. Runs summary
    res_runs = session.execute(text("""
        SELECT 
            run_id, 
            status, 
            records_scraped, 
            started_at, 
            completed_at,
            target_route_id,
            target_window_id,
            raw_artifact_uri,
            error_summary
        FROM collection_runs 
        WHERE run_id >= 297 
        ORDER BY run_id ASC;
    """)).mappings().all()

    print(f"Total runs in DB (run_id >= 297): {len(res_runs)}")
    completed_runs = [r for r in res_runs if r["status"] == "COMPLETED"]
    running_runs = [r for r in res_runs if r["status"] in ("RUNNING", "IN_PROGRESS")]
    failed_runs = [r for r in res_runs if r["status"] == "FAILED"]
    print(f"Completed runs: {len(completed_runs)}")
    print(f"Running/In-progress runs: {len(running_runs)}")
    print(f"Failed runs: {len(failed_runs)}")

    if completed_runs:
        last_completed = completed_runs[-1]
        print(f"\nLast COMPLETED run:")
        print(f"  run_id: {last_completed['run_id']}")
        print(f"  target_route_id: {last_completed['target_route_id']}")
        print(f"  target_window_id: {last_completed['target_window_id']}")
        print(f"  raw_artifact_uri: {last_completed['raw_artifact_uri']}")
        print(f"  records_scraped: {last_completed['records_scraped']}")
        print(f"  completed_at: {last_completed['completed_at']}")
    
    if running_runs:
        print(f"\nInterrupted / Non-completed runs ({len(running_runs)}):")
        for r in running_runs:
            print(f"  run_id={r['run_id']}, status={r['status']}, route_id={r['target_route_id']}, window_id={r['target_window_id']}, started_at={r['started_at']}, raw_artifact_uri={r['raw_artifact_uri']}")

    # 2. Total observations persisted for run_id >= 297
    res_obs = session.execute(text("""
        SELECT 
            COUNT(*) AS total_obs,
            COUNT(DISTINCT run_id) AS runs_with_obs,
            COUNT(DISTINCT fingerprint_hash) AS distinct_fingerprints,
            MIN(created_at) AS earliest_obs,
            MAX(created_at) AS latest_obs
        FROM fare_observations
        WHERE run_id >= 297;
    """)).mappings().one()

    print(f"\nPersisted Observations (run_id >= 297):")
    print(f"  Total observations: {res_obs['total_obs']}")
    print(f"  Runs with observations: {res_obs['runs_with_obs']}")
    print(f"  Distinct fingerprints: {res_obs['distinct_fingerprints']}")
    print(f"  Earliest observation: {res_obs['earliest_obs']}")
    print(f"  Latest observation: {res_obs['latest_obs']}")

    # 3. Check route codes for completed runs
    res_route_info = session.execute(text("""
        SELECT 
            cr.run_id, 
            r.route_code, 
            bw.window_code, 
            cr.raw_artifact_uri, 
            cr.status, 
            cr.records_scraped, 
            COUNT(fo.observation_id) AS actual_obs_count,
            cr.completed_at
        FROM collection_runs cr
        JOIN routes r ON cr.target_route_id = r.route_id
        JOIN booking_windows bw ON cr.target_window_id = bw.window_id
        LEFT JOIN fare_observations fo ON fo.run_id = cr.run_id
        WHERE cr.run_id >= 297
        GROUP BY cr.run_id, r.route_code, bw.window_code, cr.raw_artifact_uri, cr.status, cr.records_scraped, cr.completed_at
        ORDER BY cr.run_id ASC;
    """)).mappings().all()

    print(f"\nBreakdown by run ({len(res_route_info)} runs):")
    for row in res_route_info:
        print(f"  Run {row['run_id']}: {row['route_code']:<8} {row['window_code']:<5} -> status={row['status']}, scraped={row['records_scraped']}, db_obs={row['actual_obs_count']}, completed_at={row['completed_at']}")

    # 4. Check if anything ran after 17:05 (when laptop reopened around 17:20)
    res_recent = session.execute(text("""
        SELECT count(*) AS count_after_1705 
        FROM collection_runs 
        WHERE started_at > '2026-09-15 17:05:00+05:30'::timestamptz;
    """)).mappings().one()
    print(f"\nRuns created after 17:05:00: {res_recent['count_after_1705']}")

finally:
    session.close()
