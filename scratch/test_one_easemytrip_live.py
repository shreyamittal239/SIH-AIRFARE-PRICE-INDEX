import sys
from pathlib import Path
from datetime import date, timedelta
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.easemytrip_collector import EaseMyTripCollector

bm = BrowserManager(headless=True)
try:
    collector = EaseMyTripCollector(browser_manager=bm)
    origin = "DEL"
    destination = "BOM"
    travel_date = date.today() + timedelta(days=7)
    
    print(f"Testing EaseMyTrip live search: {origin} -> {destination} on {travel_date}...")
    t0 = time.time()
    quotes = collector.collect(origin=origin, destination=destination, travel_date=travel_date)
    dur = time.time() - t0
    print(f"Extraction completed in {dur:.2f}s!")
    print(f"Extracted {len(quotes)} quotes.")
    if quotes:
        for i, q in enumerate(quotes[:5], 1):
            print(f"  [{i}] {q.airline} {q.flight_number} | {q.departure_time}-{q.arrival_time} | ₹{q.total_fare} | stops={q.stops}")
    else:
        print("0 quotes extracted.")
finally:
    collector.close()
    bm.close()
    print(f"Contexts remaining in BrowserManager: {len(bm._contexts)}")
