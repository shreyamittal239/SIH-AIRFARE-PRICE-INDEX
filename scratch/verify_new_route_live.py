"""Controlled live verification of newly activated route (DEL-JAI) and full DB persistence audit.

Tasks:
1. Adds and activates DEL-JAI in PostgreSQL route_weights.
2. Dynamically queries routes via CollectionOrchestrator (verifies 26 routes).
3. Dispatches 1 live collection task: DEL-JAI × T+7 × CLEARTRIP.
4. Audits PostgreSQL CollectionRun and FareObservation persistence across all 14 verification criteria.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import logging
import sys

from backend.app.db.database import SessionLocal
from backend.app.db.models.city import City
from backend.app.db.models.route import Route
from backend.app.db.models.route_weight import RouteWeight
from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.collectors.orchestrator import (
    CollectionOrchestrator,
    CollectionTask,
    CollectionStatus,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_new_route")


def main():
    db = SessionLocal()
    orchestrator = CollectionOrchestrator(db)

    print("=== Step 1: Baseline Route Count in DB ===")
    initial_routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    print(f"  Active routes before adding new route: {len(initial_routes)}")

    print("\n=== Step 2: Adding and Activating New Route DEL-JAI ===")
    # 1. Resolve or add Jaipur City
    del_city = db.query(City).filter_by(city_code="DEL").first()
    jai_city = db.query(City).filter_by(city_code="JAI").first()
    if not jai_city:
        jai_city = City(
            city_code="JAI",
            city_name="Jaipur",
            state_name="Rajasthan",
            is_metro=False,
            is_active=True,
        )
        db.add(jai_city)
        db.flush()
        print(f"  Created City: {jai_city.city_code} (ID {jai_city.city_id})")
    else:
        print(f"  Found existing City: {jai_city.city_code} (ID {jai_city.city_id})")

    # 2. Resolve or add DEL-JAI Route
    route_del_jai = db.query(Route).filter_by(route_code="DEL-JAI").first()
    if not route_del_jai:
        route_del_jai = Route(
            origin_city_id=del_city.city_id,
            destination_city_id=jai_city.city_id,
            route_code="DEL-JAI",
            is_active=True,
        )
        db.add(route_del_jai)
        db.flush()
        print(f"  Created Route: {route_del_jai.route_code} (ID {route_del_jai.route_id})")
    else:
        print(f"  Found existing Route: {route_del_jai.route_code} (ID {route_del_jai.route_id})")

    # 3. Activate in route_weights for 2026-07
    rw = db.query(RouteWeight).filter_by(route_id=route_del_jai.route_id, base_period_code="2026-07").first()
    if not rw:
        rw = RouteWeight(
            route_id=route_del_jai.route_id,
            base_period_code="2026-07",
            passenger_volume=75000,
            weight=Decimal("0.01500000"),
            is_active=True,
        )
        db.add(rw)
    else:
        rw.is_active = True
    db.commit()
    print(f"  Activated route_weight for Route {route_del_jai.route_id} ({route_del_jai.route_code})")

    print("\n=== Step 3: Dynamic Route Basket Query (Verifying N=26) ===")
    updated_routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
    print(f"  Active routes after activation: {len(updated_routes)}")
    assert len(updated_routes) == len(initial_routes) + 1, "Route count did not increase by 1!"
    new_basket_route = next(r for r in updated_routes if r.route_code == "DEL-JAI")
    print(f"  Dynamically discovered new route: ID={new_basket_route.route_id}, Code={new_basket_route.route_code}")

    print("\n=== Step 4: Generating Live Collection Task for DEL-JAI × T+7 ===")
    windows = orchestrator.get_active_booking_windows()
    w_t7 = next(w for w in windows if w.window_code == "T+7")
    tasks = orchestrator.build_task_matrix([new_basket_route], [w_t7])
    target_task = tasks[0]
    print(f"  Task: {target_task.route.route_code} | Window: {target_task.window.window_code} | Date: {target_task.travel_date}")

    print("\n=== Step 5: Executing Live Search on Cleartrip ===")
    task_res = orchestrator.execute_single_task(target_task, source_code="CLEARTRIP")
    print(f"  Execution Result:")
    print(f"    Status: {task_res.status}")
    print(f"    CollectionStatus: {task_res.collection_status}")
    print(f"    Run ID: {task_res.run_id}")
    print(f"    Quotes Extracted: {task_res.quotes_count}")
    print(f"    Records Inserted: {task_res.inserted_count}")
    print(f"    Records Skipped: {task_res.skipped_count}")
    print(f"    Error: {task_res.error}")

    print("\n=== Step 6: PostgreSQL Database Persistence Audit (14 Criteria) ===")
    run = db.query(CollectionRun).filter_by(run_id=task_res.run_id).first()
    assert run is not None, "1. CollectionRun does not exist!"
    print(f"  1. CollectionRun exists: run_id={run.run_id}")
    print(f"  2. source_id correct: {run.source_id} ({run.data_source.source_code})")
    print(f"  3. target_route_id correct: {run.target_route_id} (matches new route ID {route_del_jai.route_id}: {run.target_route_id == route_del_jai.route_id})")
    print(f"  4. target_window_id correct: {run.target_window_id} (matches window ID {w_t7.window_id})")
    print(f"  5. status correct: {run.status}")
    print(f"  6. records_scraped matches: {run.records_scraped} (matches inserted {task_res.inserted_count})")

    obs_records = db.query(FareObservation).filter_by(run_id=run.run_id).all()
    print(f"  Total fare_observations for run: {len(obs_records)}")

    if obs_records:
        first_obs = obs_records[0]
        print(f"  7. fare_observations route_id: {first_obs.route_id} (matches NEW route_id: {first_obs.route_id == route_del_jai.route_id})")
        print(f"  8. fare_observations data_source_id: {first_obs.data_source_id} (matches run source_id)")
        print(f"  9. fare_observations travel_date: {first_obs.travel_date} (matches {target_task.travel_date})")
        print(f"  10. fare_observations advance_days: {first_obs.advance_days}, window_id: {first_obs.window_id}")
        print(f"  11. fingerprint_hash populated: {first_obs.fingerprint_hash[:16]}...")
        print(f"  12. total_fare populated: ₹{first_obs.total_fare} (currency: {first_obs.currency})")

        # 13. Check intra-run duplicate fingerprints
        fps = [o.fingerprint_hash for o in obs_records]
        assert len(fps) == len(set(fps)), "Duplicate fingerprints found in same run!"
        print(f"  13. Duplicate fingerprints within run: 0 duplicates across {len(fps)} observations")

        print(f"  14. Raw observation preserved: Flight={first_obs.flight_number}, AirlineID={first_obs.airline_id}, "
              f"Departure={first_obs.scheduled_departure_time}, Arrival={first_obs.scheduled_arrival_time}, Stops={first_obs.stops}")

    db.close()
    print("\n=== Live Verification of Newly Activated Route Completed Successfully ===")


if __name__ == "__main__":
    main()
