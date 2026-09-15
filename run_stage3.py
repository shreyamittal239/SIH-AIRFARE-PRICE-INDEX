from backend.app.db.database import SessionLocal
from backend.app.db.models.index_daily import IndexDaily
from backend.app.db.models.route import Route
from backend.app.db.models.booking_window import BookingWindow
from backend.processing.aggregation.route_level_index_service import RouteLevelIndexService
from sqlalchemy import select, text

def run_stage3():
    with SessionLocal() as session:
        # Run Stage 3
        service = RouteLevelIndexService(db=session)
        count = service.compute_route_indices(base_period_code="JUL-2026")
        print(f"Stage 3 Executed: {count} ROUTE_LEVEL records processed.")
        
        # Manual Verification for DEL-BOM
        route_del_bom = session.scalar(select(Route).where(Route.route_code == "DEL-BOM")).route_id
        
        print("\n--- Manual Calculation for DEL-BOM ---")
        # Get all WINDOW_COMPOSITE values for this route
        window_stmt = select(IndexDaily.window_id, IndexDaily.price_relative).where(
            IndexDaily.route_id == route_del_bom,
            IndexDaily.index_level == "WINDOW_COMPOSITE",
            IndexDaily.base_period_code == "JUL-2026"
        )
        window_values = session.execute(window_stmt).all()
        
        total_sum = Decimal("0")
        for win_id, val in window_values:
            print(f"Window {win_id} Price Relative: {val}")
            total_sum += val
        
        count_windows = len(window_values)
        calculated_index = total_sum / Decimal(count_windows)
        
        print(f"Sum: {total_sum}")
        print(f"Count: {count_windows}")
        print(f"Calculated Route Index: {calculated_index}")
        
        # Get stored ROUTE_LEVEL value
        route_stmt = select(IndexDaily).where(
            IndexDaily.route_id == route_del_bom,
            IndexDaily.index_level == "ROUTE_LEVEL",
            IndexDaily.base_period_code == "JUL-2026"
        )
        stored_row = session.scalar(route_stmt)
        print(f"Stored ROUTE_LEVEL index_value: {stored_row.index_value}")
        
        # Verify WINDOW_COMPOSITE remain unchanged (simple count check)
        total_window_comp = session.scalar(text("SELECT count(*) FROM index_daily WHERE index_level = 'WINDOW_COMPOSITE'"))
        print(f"\nTotal WINDOW_COMPOSITE rows: {total_window_comp}")

from decimal import Decimal
if __name__ == "__main__":
    run_stage3()
