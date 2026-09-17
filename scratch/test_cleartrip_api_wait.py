import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date, timedelta
from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.cleartrip_collector import CleartripCollector

bm = BrowserManager(headless=True)
try:
    collector = CleartripCollector(browser_manager=bm)
    # Test DEL -> BOM T+7
    t_date = date.today() + timedelta(days=7)
    print(f"Testing collection for DEL -> BOM on {t_date}...")
    quotes = collector.collect(origin="DEL", destination="BOM", travel_date=t_date)
    print(f"SUCCESS: Extracted {len(quotes)} quotes!")
    if quotes:
        print(f"Sample quote: {quotes[0].airline} {quotes[0].flight_number} - INR {quotes[0].total_fare}")
finally:
    bm.close()
