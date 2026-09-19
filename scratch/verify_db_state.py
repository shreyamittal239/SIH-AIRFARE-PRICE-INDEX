"""Database state verification after one-shot run."""

import os
import sys

sys.path.insert(0, os.path.abspath("."))

from backend.app.db.database import SessionLocal
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route import Route
from backend.app.db.models.booking_window import BookingWindow
from sqlalchemy import func, select


def main():
    session = SessionLocal()
    try:
        # 1. Orphan RUNNING runs check
        running_count = session.scalar(
            select(func.count()).select_from(CollectionRun).where(CollectionRun.status == "RUNNING")
        )
        print(f"Orphan RUNNING runs: {running_count}")

        # 2. Check the latest run (1202)
        latest_run = session.scalar(
            select(CollectionRun).order_by(CollectionRun.run_id.desc()).limit(1)
        )
        print(f"Latest Run ID: {latest_run.run_id}")
        print(f"Status: {latest_run.status}")
        print(f"Records Scraped: {latest_run.records_scraped}")
        print(f"Source ID: {latest_run.source_id}")

        # 3. Check observations for latest run
        obs_list = session.scalars(
            select(FareObservation).where(FareObservation.run_id == latest_run.run_id)
        ).all()
        print(f"Persisted observations count: {len(obs_list)}")
        print(f"Persistence parity: {len(obs_list)} == {latest_run.records_scraped} ({len(obs_list) == latest_run.records_scraped})")

        # 4. Check route and window details
        for obs in obs_list:
            route = session.get(Route, obs.route_id)
            window = session.get(BookingWindow, obs.window_id)
            print(f"  Obs ID: {obs.observation_id}")
            print(f"  Route: {route.route_code} (ID: {obs.route_id})")
            print(f"  Window: {window.window_code} (ID: {obs.window_id})")
            print(f"  Travel Date: {obs.travel_date}")
            print(f"  Observed At: {obs.observed_at}")
            print(f"  Total Fare: INR {obs.total_fare}")
            print(f"  Fingerprint Hash: {obs.fingerprint_hash[:24]}...")

        # 5. Check global duplicate fingerprints in fare_observations
        dup_fingerprints = session.execute(
            select(FareObservation.fingerprint_hash, func.count())
            .group_by(FareObservation.fingerprint_hash)
            .having(func.count() > 1)
        ).all()
        print(f"Total duplicate fingerprints across all fare observations: {len(dup_fingerprints)}")

    finally:
        session.close()


if __name__ == "__main__":
    main()
