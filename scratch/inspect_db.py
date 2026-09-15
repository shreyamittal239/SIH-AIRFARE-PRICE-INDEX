import os
from sqlalchemy import select, func, text
from backend.app.db.database import SessionLocal
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route_weight import RouteWeight
from backend.app.db.models.base_period_fare import BasePeriodFare
from backend.app.db.models.dgca_traffic_data import DGCATrafficData

def inspect():
    with SessionLocal() as db:
        # 1. Row counts
        tables = {
            "fare_observations": FareObservation,
            "route_weights": RouteWeight,
            "base_period_fares": BasePeriodFare,
            "dgca_traffic_data": DGCATrafficData,
        }
        
        print("--- Row Counts ---")
        for name, model in tables.items():
            count = db.scalar(select(func.count()).select_from(model))
            print(f"{name}: {count}")

        # 2. Quality status distribution
        print("\n--- Quality Status Distribution ---")
        results = db.execute(text("SELECT quality_status, count(*) FROM fare_observations GROUP BY quality_status")).all()
        for status, count in results:
            print(f"{status}: {count}")

        # 3. Fare class and stops/cabin population
        print("\n--- Null Check (FareObservation) ---")
        null_checks = [
            "cabin_class", "fare_family", "stops", "origin_airport_code", "destination_airport_code"
        ]
        for col in null_checks:
            null_count = db.scalar(text(f"SELECT count(*) FROM fare_observations WHERE {col} IS NULL"))
            total_count = db.scalar(text("SELECT count(*) FROM fare_observations"))
            print(f"{col}: {null_count}/{total_count} null")

        # 4. DGCA average fares check
        # The user asked if there's a table storing DGCA's published average fares (not just traffic).
        # I'll list all tables to be sure.
        print("\n--- All Tables ---")
        all_tables = db.execute(text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'")).all()
        for (table,) in all_tables:
            print(table)

        # 5. Example row from fare_observations
        print("\n--- Example Row (fare_observations) ---")
        row = db.execute(text("SELECT * FROM fare_observations LIMIT 1")).first()
        if row:
            print(row)
        else:
            print("No rows found.")

if __name__ == "__main__":
    try:
        inspect()
    except Exception as e:
        print(f"Error: {e}")
