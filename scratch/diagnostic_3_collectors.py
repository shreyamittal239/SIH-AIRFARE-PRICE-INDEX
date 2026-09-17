"""Quick diagnostic script to verify 1 live task for each of the 3 collectors:
1. Yatra (DEL-BOM, T+7)
2. Air India Express (DEL-BOM, T+7)
3. SpiceJet (DEL-BOM, T+7)
"""

from datetime import date, timedelta
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("diagnostic_3_collectors")

from backend.app.db.database import SessionLocal
from backend.collectors.orchestrator import CollectionOrchestrator, CollectionTask
from backend.collectors.playwright.browser import BrowserManager



def test_collectors():
    session = SessionLocal()
    orchestrator = CollectionOrchestrator(db=session)

    # Route: DEL-BOM
    routes = [r for r in orchestrator.get_active_basket_routes() if r.route_code == "DEL-BOM"]
    assert len(routes) == 1, "DEL-BOM not found"
    route = routes[0]

    # Window: T+7
    windows = [w for w in orchestrator.get_active_booking_windows() if w.window_code == "T+7"]
    assert len(windows) == 1, "T+7 not found"
    window = windows[0]

    obs_date = date(2026, 9, 16)
    travel_date = obs_date + timedelta(days=window.target_advance_days)

    task = CollectionTask(
        route=route,
        window=window,
        travel_date=travel_date,
        observation_date=obs_date,
    )

    results = {}

    # 1. Test Air India Express (headless=True)
    logger.info("=== Testing Air India Express ===")
    bm_aix = BrowserManager(headless=True, timeout_ms=35000)
    try:
        res_aix = orchestrator.execute_single_task(task, source_code="AIR_INDIA_EXPRESS", browser_manager=bm_aix)
        results["AIR_INDIA_EXPRESS"] = (res_aix.status, res_aix.quotes_count, res_aix.inserted_count, res_aix.error)
    finally:
        bm_aix.close()

    # 2. Test Yatra (headless=False)
    logger.info("=== Testing Yatra ===")
    bm_yatra = BrowserManager(headless=False, timeout_ms=45000)
    try:
        res_yatra = orchestrator.execute_single_task(task, source_code="YATRA", browser_manager=bm_yatra)
        results["YATRA"] = (res_yatra.status, res_yatra.quotes_count, res_yatra.inserted_count, res_yatra.error)
    finally:
        bm_yatra.close()

    # 3. Test SpiceJet (headless=False)
    logger.info("=== Testing SpiceJet ===")
    bm_sj = BrowserManager(headless=False, timeout_ms=40000)
    try:
        res_sj = orchestrator.execute_single_task(task, source_code="SPICEJET", browser_manager=bm_sj)
        results["SPICEJET"] = (res_sj.status, res_sj.quotes_count, res_sj.inserted_count, res_sj.error)
    finally:
        bm_sj.close()

    session.close()

    print("\n" + "=" * 60)
    print("DIAGNOSTIC RESULTS:")
    for src, res in results.items():
        print(f"  {src:20}: Status={res[0]}, Quotes={res[1]}, Inserted={res[2]}, Error={res[3]}")
    print("=" * 60)


if __name__ == "__main__":
    test_collectors()
