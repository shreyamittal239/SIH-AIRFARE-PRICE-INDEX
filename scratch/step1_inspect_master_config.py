"""Step 1: Inspect master route basket and booking windows from PostgreSQL."""
import sys
from pathlib import Path

# Safe UTF-8 console output
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.db.database import SessionLocal
from backend.collectors.orchestrator import CollectionOrchestrator

def main():
    session = SessionLocal()
    try:
        orchestrator = CollectionOrchestrator(db=session)

        routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
        windows = orchestrator.get_active_booking_windows()
        windows.sort(key=lambda w: w.display_order)

        print(f"=== MASTER ROUTE BASKET INSPECTION ===")
        print(f"Active Master Routes Count: {len(routes)}")
        for idx, r in enumerate(routes, 1):
            print(f"  {idx:>2}. Route ID {r.route_id:>3}: {r.route_code:<8} ({r.origin_code} -> {r.destination_code}) | Weight: {r.weight:.4f} | Pax: {r.passenger_volume:,}")

        print(f"\n=== MASTER BOOKING WINDOWS INSPECTION ===")
        print(f"Active Booking Windows Count: {len(windows)}")
        for idx, w in enumerate(windows, 1):
            print(f"  {idx}. Window ID {w.window_id:>2}: {w.window_code:<6} | Target Advance Days: {w.target_advance_days:>2} | Display Order: {w.display_order}")

        # Verify exact master configuration expectations
        expected_routes_count = 25
        expected_window_codes = ["T+1", "T+7", "T+15", "T+30", "T+45"]
        actual_window_codes = [w.window_code for w in windows]

        print("\n=== CONFIGURATION VALIDATION ===")
        routes_valid = (len(routes) == expected_routes_count)
        windows_valid = (actual_window_codes == expected_window_codes)

        print(f"Routes valid (expected {expected_routes_count}): {routes_valid} ({len(routes)})")
        print(f"Windows valid (expected {expected_window_codes}): {windows_valid} ({actual_window_codes})")

        if not routes_valid or not windows_valid:
            print("\n[ALERT] Database does NOT match expected master configuration!")
            sys.exit(1)
        else:
            print("\n[OK] Master configuration exactly matches expected state.")

    finally:
        session.close()

if __name__ == "__main__":
    main()
