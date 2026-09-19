"""Read-only database integrity audit for the targeted 12-task SpiceJet revalidation."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func
from backend.app.db.database import SessionLocal
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route import Route
from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.data_source import DataSource


def audit_targeted_12():
    results_file = Path("scratch/spicejet_targeted_12_results.json")
    if not results_file.exists():
        print(f"Error: {results_file} not found.")
        return False

    with open(results_file, "r", encoding="utf-8") as f:
        tasks = json.load(f)

    run_ids = [t["run_id"] for t in tasks if t.get("run_id") is not None]
    print(f"Auditing {len(run_ids)} targeted runs: {run_ids}")

    session = SessionLocal()
    try:
        # 1. No orphan RUNNING runs
        running_runs = session.scalars(
            select(CollectionRun).where(
                CollectionRun.run_id.in_(run_ids),
                CollectionRun.status == "RUNNING"
            )
        ).all()
        check1 = len(running_runs) == 0
        print(f"[CHECK 1] Orphan RUNNING runs: {len(running_runs)} (PASS={check1})")

        # Load all runs
        runs = session.scalars(
            select(CollectionRun).where(CollectionRun.run_id.in_(run_ids))
        ).all()
        run_map = {r.run_id: r for r in runs}

        # 2. No route_id / target_route_id mismatches
        # 3. No window_id mismatches
        # 4. No data_source_id mismatches
        route_mismatches = []
        window_mismatches = []
        source_mismatches = []
        date_mismatches = []
        persistence_parity_failures = []

        total_ingested_quotes = sum(t["inserted_count"] for t in tasks)
        total_db_observations = 0

        all_fingerprints = []

        for t in tasks:
            rid = t["run_id"]
            run = run_map.get(rid)
            if not run:
                print(f"WARNING: Run {rid} not found in DB")
                continue

            # Check target route
            expected_route = session.scalar(select(Route).where(Route.route_code == t["route_code"]))
            if run.target_route_id != expected_route.route_id:
                route_mismatches.append((rid, run.target_route_id, expected_route.route_id))

            # Check target window
            expected_window = session.scalar(select(BookingWindow).where(BookingWindow.window_code == t["window_code"]))
            if run.target_window_id != expected_window.window_id:
                window_mismatches.append((rid, run.target_window_id, expected_window.window_id))

            # Check observations for this run
            obs_list = session.scalars(
                select(FareObservation).where(FareObservation.run_id == rid)
            ).all()
            total_db_observations += len(obs_list)

            if len(obs_list) != t["inserted_count"]:
                persistence_parity_failures.append((rid, len(obs_list), t["inserted_count"]))

            for obs in obs_list:
                # Check data_source_id
                if obs.data_source_id != run.source_id:
                    source_mismatches.append((obs.observation_id, obs.data_source_id, run.source_id))
                # Check route_id
                if obs.route_id != run.target_route_id:
                    route_mismatches.append((obs.observation_id, obs.route_id, run.target_route_id))
                # Check travel date matches observation date + advance days
                expected_advance = expected_window.target_advance_days
                if obs.advance_days != expected_advance:
                    date_mismatches.append((obs.observation_id, obs.advance_days, expected_advance))
                all_fingerprints.append(obs.fingerprint_hash)

        check2 = len(route_mismatches) == 0
        print(f"[CHECK 2] Route ID mismatches: {len(route_mismatches)} (PASS={check2})")

        check3 = len(window_mismatches) == 0
        print(f"[CHECK 3] Window ID mismatches: {len(window_mismatches)} (PASS={check3})")

        check4 = len(source_mismatches) == 0
        print(f"[CHECK 4] Data Source ID mismatches: {len(source_mismatches)} (PASS={check4})")

        check5 = len(date_mismatches) == 0
        print(f"[CHECK 5] Date calculation mismatches: {len(date_mismatches)} (PASS={check5})")

        check6 = len(persistence_parity_failures) == 0 and (total_db_observations == total_ingested_quotes)
        print(f"[CHECK 6] Persistence Parity: DB={total_db_observations} == Ingested={total_ingested_quotes} (PASS={check6})")

        # Check duplicate fingerprints
        duplicate_fps = len(all_fingerprints) - len(set(all_fingerprints))
        check7 = duplicate_fps == 0
        print(f"[CHECK 7] Duplicate Fingerprints: {duplicate_fps} (PASS={check7})")

        # Check invalid records (null fares, invalid status, etc.)
        invalid_records = session.scalars(
            select(FareObservation).where(
                FareObservation.run_id.in_(run_ids),
                (FareObservation.total_fare <= 0) | (FareObservation.quality_status != "VALID")
            )
        ).all()
        check8 = len(invalid_records) == 0
        print(f"[CHECK 8] Invalid fare records: {len(invalid_records)} (PASS={check8})")

        all_passed = all([check1, check2, check3, check4, check5, check6, check7, check8])
        print(f"\nOVERALL DATABASE INTEGRITY AUDIT: {'PASS' if all_passed else 'FAIL'}")
        return all_passed

    finally:
        session.close()


if __name__ == "__main__":
    audit_targeted_12()
