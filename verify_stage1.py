from backend.app.db.database import SessionLocal
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route_daily_summary import RouteDailySummary
from backend.processing.aggregation.route_daily_summary_service import RouteDailySummaryService
from sqlalchemy import select

def run_stage1():
    with SessionLocal() as session:
        # Initialize service
        service = RouteDailySummaryService(db=session)
        
        # Query all observations for processing
        observations = session.scalars(select(FareObservation)).all()
        
        # Process and persist
        result = service.process_and_persist(observations)
        print(f"Stage 1 Complete: {result.summaries_created} summaries created, {result.summaries_updated} updated.")
        
        # Verify DEL-BOM + T+7
        # Seed route_id=8, window_id=2 (based on previous seed output)
        # But let's query them to be safe.
        from backend.app.db.models.route import Route
        from backend.app.db.models.booking_window import BookingWindow
        
        route_del_bom = session.scalar(select(Route).where(Route.route_code == "DEL-BOM")).route_id
        window_t7 = session.scalar(select(BookingWindow).where(BookingWindow.window_code == "T+7")).window_id
        
        route_blr_del = session.scalar(select(Route).where(Route.route_code == "BLR-DEL")).route_id
        window_t1 = session.scalar(select(BookingWindow).where(BookingWindow.window_code == "T+1")).window_id
        
        print("\n--- Verification: DEL-BOM + T+7 ---")
        stmt = select(RouteDailySummary).where(
            RouteDailySummary.route_id == route_del_bom,
            RouteDailySummary.window_id == window_t7
        )
        row = session.scalar(stmt)
        if row:
            print(f"count: {row.observations_count}")
            print(f"geo_mean: {row.geometric_mean_fare}")
            print(f"is_imputed: {row.is_imputed}")
        else:
            print("Row not found!")

        print("\n--- Verification: BLR-DEL + T+1 ---")
        stmt = select(RouteDailySummary).where(
            RouteDailySummary.route_id == route_blr_del,
            RouteDailySummary.window_id == window_t1
        )
        row = session.scalar(stmt)
        if row:
            print(f"count: {row.observations_count}")
            print(f"is_imputed: {row.is_imputed}")
        else:
            print("Row not found!")

if __name__ == "__main__":
    run_stage1()
