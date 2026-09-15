from backend.app.db.database import SessionLocal
from sqlalchemy import text

def verify():
    with SessionLocal() as session:
        tables = [
            "cities", "routes", "airlines", "data_sources", 
            "booking_windows", "collection_runs", "fare_observations", 
            "base_period_fares", "route_weights", "booking_window_weights", 
            "dgca_fare_benchmark"
        ]
        
        print("--- Row Counts ---")
        for table in tables:
            count = session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            print(f"{table}: {count}")
        
        print("\n--- Fare Observations (Sample) ---")
        obs = session.execute(text("SELECT observation_id, route_id, total_fare, quality_status, is_available FROM fare_observations LIMIT 5")).all()
        for row in obs:
            print(row)
            
        print("\n--- Route Daily Summary (Sample) ---")
        summs = session.execute(text("SELECT * FROM route_daily_summary LIMIT 5")).all()
        print(summs if summs else "Empty (Expected)")
        
        print("\n--- Index Daily (Sample) ---")
        indices = session.execute(text("SELECT * FROM index_daily LIMIT 5")).all()
        print(indices if indices else "Empty (Expected)")

if __name__ == "__main__":
    verify()
