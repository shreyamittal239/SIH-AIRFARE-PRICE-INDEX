
from backend.app.db.database import SessionLocal
from backend.processing.aggregation.national_index_service import NationalIndexService
from sqlalchemy import select
from backend.app.db.models.index_daily import IndexDaily
from backend.app.db.models.route_weight import RouteWeight

def verify_stage4():
    with SessionLocal() as db:
        base_period = "JUL-2026"
        service = NationalIndexService(db)

        print(f"Running Stage 4 for period {base_period}...")
        count = service.compute_national_indices(base_period)
        print(f"Processed {count} dates.")

        # Now extract data for manual report
        # Get all ROUTE_LEVEL and NATIONAL_COMPOSITE for the period
        stmt = select(IndexDaily).where(
            IndexDaily.base_period_code == base_period,
            IndexDaily.index_level.in_(["ROUTE_LEVEL", "NATIONAL_COMPOSITE"])
        ).order_by(IndexDaily.index_date, IndexDaily.index_level.desc())

        results = db.scalars(stmt).all()

        print("\n--- VERIFICATION DATA ---")
        current_date = None
        for rec in results:
            if rec.index_date != current_date:
                current_date = rec.index_date
                print(f"\nDate: {current_date}")

            if rec.index_level == "ROUTE_LEVEL":
                # Find weight
                w_stmt = select(RouteWeight.weight).where(
                    RouteWeight.route_id == rec.route_id,
                    RouteWeight.base_period_code == base_period
                )
                weight = db.scalar(w_stmt)
                print(f"  Route {rec.route_id}: Index={rec.index_value}, Weight={weight}")
            else:
                print(f"  NATIONAL_COMPOSITE: Value={rec.index_value}, RoutesIncluded={rec.routes_included}")

if __name__ == "__main__":
    verify_stage4()
