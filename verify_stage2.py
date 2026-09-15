from backend.app.db.database import SessionLocal
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route_daily_summary import RouteDailySummary
from backend.app.db.models.index_daily import IndexDaily
from backend.app.db.models.route import Route
from backend.app.db.models.booking_window import BookingWindow
from backend.processing.aggregation.route_daily_summary_service import RouteDailySummaryService
from backend.processing.aggregation.price_relative_service import PriceRelativeService
from sqlalchemy import select

def run_pipeline():
    with SessionLocal() as session:
        # --- Stage 1: Aggregation ---
        agg_service = RouteDailySummaryService(db=session)
        observations = session.scalars(select(FareObservation)).all()
        agg_service.process_and_persist(observations)
        
        # --- Stage 2: Price Relative ---
        rel_service = PriceRelativeService(db=session)
        rel_service.compute_relatives(base_period_code="JUL-2026")
        
        # --- Verification ---
        route_del_bom = session.scalar(select(Route).where(Route.route_code == "DEL-BOM")).route_id
        window_t7 = session.scalar(select(BookingWindow).where(BookingWindow.window_code == "T+7")).window_id
        
        route_blr_del = session.scalar(select(Route).where(Route.route_code == "BLR-DEL")).route_id
        window_t1 = session.scalar(select(BookingWindow).where(BookingWindow.window_code == "T+1")).window_id
        
        print("\n--- Verification: DEL-BOM + T+7 ---")
        stmt = select(IndexDaily).where(
            IndexDaily.route_id == route_del_bom,
            IndexDaily.window_id == window_t7,
            IndexDaily.index_level == "WINDOW_COMPOSITE"
        )
        row = session.scalar(stmt)
        if row:
            print(f"index_value: {row.index_value}")
            print(f"price_relative: {row.price_relative}")
            print(f"index_level: {row.index_level}")
            print(f"route_id: {row.route_id}")
            print(f"window_id: {row.window_id}")
            print(f"formula_type: {row.formula_type}")
            
            # Check imputation status from Stage 1
            summ = session.scalar(select(RouteDailySummary).where(
                RouteDailySummary.route_id == route_del_bom,
                RouteDailySummary.window_id == window_t7
            ))
            print(f"underlying_is_imputed: {summ.is_imputed}")
        else:
            print("Row not found!")

        print("\n--- Verification: BLR-DEL + T+1 ---")
        stmt = select(IndexDaily).where(
            IndexDaily.route_id == route_blr_del,
            IndexDaily.window_id == window_t1,
            IndexDaily.index_level == "WINDOW_COMPOSITE"
        )
        row = session.scalar(stmt)
        if row:
            print(f"index_value: {row.index_value}")
            print(f"price_relative: {row.price_relative}")
            print(f"index_level: {row.index_level}")
            print(f"route_id: {row.route_id}")
            print(f"window_id: {row.window_id}")
            print(f"formula_type: {row.formula_type}")
            
            # Check imputation status from Stage 1
            summ = session.scalar(select(RouteDailySummary).where(
                RouteDailySummary.route_id == route_blr_del,
                RouteDailySummary.window_id == window_t1
            ))
            print(f"underlying_is_imputed: {summ.is_imputed}")
        else:
            print("Row not found!")

if __name__ == "__main__":
    run_pipeline()
