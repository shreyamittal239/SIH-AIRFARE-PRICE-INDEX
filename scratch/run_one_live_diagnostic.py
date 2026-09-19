import sys
from pathlib import Path
from datetime import date, timedelta
import json
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.db.database import SessionLocal
from backend.app.db.models.fare_observation import FareObservation
from backend.collectors.orchestrator import CollectionOrchestrator, CollectionTask
from backend.collectors.playwright.browser import BrowserManager

def main():
    print("=" * 80)
    print("STEP 2: RUN ONE LIVE CLEARTRIP DIAGNOSTIC (DEL-BOM, T+7)")
    print("=" * 80)

    session = SessionLocal()
    bm = BrowserManager(headless=True)
    try:
        orchestrator = CollectionOrchestrator(db=session)
        routes = orchestrator.get_active_basket_routes(base_period_code="2026-07")
        windows = orchestrator.get_active_booking_windows()

        route_del_bom = next(r for r in routes if r.route_code == "DEL-BOM")
        window_t7 = next(w for w in windows if w.window_code == "T+7")

        obs_date = date.today()
        travel_date = obs_date + timedelta(days=window_t7.target_advance_days)

        task = CollectionTask(
            route=route_del_bom,
            window=window_t7,
            travel_date=travel_date,
            observation_date=obs_date,
        )

        # Track network events during this run
        nav_status = None
        api_v2_status = None
        api_v2_body_snippet = None

        # Hook into page creation to capture network traffic
        orig_new_page = bm.new_page
        def instrumented_new_page(context=None):
            page = orig_new_page(context=context)
            def on_response(res):
                nonlocal nav_status, api_v2_status, api_v2_body_snippet
                if "cleartrip.com/flights/results" in res.url and nav_status is None:
                    nav_status = res.status
                if "flight/search/v2" in res.url:
                    api_v2_status = res.status
                    try:
                        api_v2_body_snippet = res.text()[:300]
                    except Exception as e:
                        api_v2_body_snippet = str(e)
            page.on("response", on_response)
            return page

        bm.new_page = instrumented_new_page

        print(f"Executing task: {task.route.route_code} {task.window.window_code} (travel_date={task.travel_date})...")
        t0 = time.time()
        res = orchestrator.execute_single_task(
            task=task,
            source_code="CLEARTRIP",
            browser_manager=bm,
        )
        duration = time.time() - t0

        print("\n" + "=" * 80)
        print("DIAGNOSTIC CAPTURE RESULTS:")
        print("=" * 80)
        print(f"1. Initial page HTTP status:         {nav_status}")
        print(f"2. flight/search/v2 HTTP status:     {api_v2_status}")
        if api_v2_body_snippet:
            print(f"   flight/search/v2 snippet:         {repr(api_v2_body_snippet)}")
        print(f"3. Task execution status:            {res.status}")
        print(f"4. Number of FlightQuotes extracted: {res.quotes_count}")
        print(f"5. Number of quotes inserted to DB:  {res.inserted_count}")
        print(f"6. Number of duplicates skipped:     {res.skipped_count}")
        print(f"7. Error message (if any):           {res.error}")
        print(f"8. CollectionRun ID:                 {res.run_id}")
        print(f"9. Execution duration:               {duration:.2f}s")
        print(f"10. Attempts made:                   {res.attempts}")

        # Check DB observations
        if res.run_id:
            obs_count = session.query(FareObservation).filter(FareObservation.run_id == res.run_id).count()
            print(f"11. DB observations persisted:       {obs_count} (Parity: {'PASS' if obs_count == res.inserted_count else 'FAIL'})")

        ctx_count = len(bm._contexts)
        print(f"12. Browser context count after task:{ctx_count} ({'CLEAN' if ctx_count == 0 else 'LEAK'})")
        print("=" * 80)

    finally:
        bm.close()
        session.close()

if __name__ == "__main__":
    main()
