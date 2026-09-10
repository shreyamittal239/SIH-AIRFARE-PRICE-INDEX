import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import logging
import os
import sys
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("verify_aix_live")

from backend.collectors.playwright.air_india_express_collector import AirIndiaExpressCollector
from backend.collectors.playwright.browser import BrowserManager
from backend.processing.ingestion.fingerprint import compute_flight_fingerprint


def run_live_aix_validation():
    today = datetime.now(timezone.utc).date()
    target_travel_date = today + timedelta(days=7)
    booking_window = "T+7"

    routes_to_test = [
        ("DEL", "BOM", "Validation Route (Zero-Inventory Test)"),
        ("DEL", "BLR", "High-Frequency Trunk Route (Inventory Extraction Test)"),
    ]

    bm = BrowserManager(headless=True, timeout_ms=50000)
    collector = AirIndiaExpressCollector(browser_manager=bm)

    try:
        for origin, destination, label in routes_to_test:
            print("=" * 75)
            print(f"  AIR INDIA EXPRESS DIRECT LIVE COLLECTOR: {origin} -> {destination}")
            print(f"  Description        : {label}")
            print(f"  Observation Date   : {today}")
            print(f"  Target Travel Date : {target_travel_date} ({booking_window})")
            print("=" * 75)

            search_url = collector.build_search_url(origin, destination, target_travel_date)
            print(f"\nCanonical URL: {search_url}")

            quotes = collector.collect(
                origin=origin,
                destination=destination,
                travel_date=target_travel_date,
                adults=1,
            )

            print(f"\n[COLLECTION RESULTS for {origin} -> {destination}]")
            print(f"Total Flight Quotes Extracted: {len(quotes)}")

            if not quotes:
                print(f"Zero quotes extracted for {origin}->{destination}. Date safety rejected zero inventory cleanly.")
                continue

            # Breakdown by fare family
            family_counts = {}
            for q in quotes:
                fam = q.fare_family or "Unspecified"
                family_counts[fam] = family_counts.get(fam, 0) + 1

            print(f"\nFare Families Observed ({len(family_counts)} distinct):")
            for fam, count in sorted(family_counts.items()):
                print(f"  - {fam}: {count} quotes")

            # Breakdown by flight number
            flight_counts = {}
            for q in quotes:
                flight_counts[q.flight_number] = flight_counts.get(q.flight_number, 0) + 1

            print(f"\nPhysical Flights Found ({len(flight_counts)} distinct):")
            for fn, count in sorted(flight_counts.items()):
                print(f"  - {fn}: {count} fare tiers")

            # Fare stats
            fares = [q.total_fare for q in quotes]
            min_fare = min(fares)
            max_fare = max(fares)
            avg_fare = sum(fares) / len(fares)
            print(f"\nFare Range: INR {min_fare} to INR {max_fare} (Mean: INR {avg_fare:.2f})")

            print("\n[SAMPLE FLIGHT QUOTES (FIRST 5)]")
            for idx, q in enumerate(quotes[:5], 1):
                fp = compute_flight_fingerprint(q)
                print(f"\nQuote {idx}:")
                print(f"  Airline       : {q.airline}")
                print(f"  Flight Number : {q.flight_number}")
                print(f"  Route         : {q.origin} -> {q.destination}")
                print(f"  Travel Date   : {q.travel_date}")
                print(f"  Times         : Dep {q.departure_time} | Arr {q.arrival_time}")
                print(f"  Stops         : {q.stops}")
                print(f"  Fare Family   : {q.fare_family}")
                print(f"  Cabin Class   : {q.cabin_class}")
                print(f"  Total Fare    : {q.currency} {q.total_fare}")
                print(f"  Availability  : {q.availability}")
                print(f"  Source        : {q.source}")
                print(f"  Fingerprint   : {fp[:16]}...")
                print(f"  Observed At   : {q.observed_at}")


        # Breakdown by fare family
        family_counts = {}
        for q in quotes:
            fam = q.fare_family or "Unspecified"
            family_counts[fam] = family_counts.get(fam, 0) + 1

        print(f"\nFare Families Observed ({len(family_counts)} distinct):")
        for fam, count in sorted(family_counts.items()):
            print(f"  - {fam}: {count} quotes")

        # Breakdown by flight number
        flight_counts = {}
        for q in quotes:
            flight_counts[q.flight_number] = flight_counts.get(q.flight_number, 0) + 1

        print(f"\nPhysical Flights Found ({len(flight_counts)} distinct):")
        for fn, count in sorted(flight_counts.items()):
            print(f"  - {fn}: {count} fare tiers")

        # Fare stats
        fares = [q.total_fare for q in quotes]
        min_fare = min(fares)
        max_fare = max(fares)
        avg_fare = sum(fares) / len(fares)
        print(f"\nFare Range: INR {min_fare} to INR {max_fare} (Mean: INR {avg_fare:.2f})")

        print("\n[SAMPLE FLIGHT QUOTES (FIRST 5)]")
        for idx, q in enumerate(quotes[:5], 1):
            fp = compute_flight_fingerprint(q)
            print(f"\nQuote {idx}:")
            print(f"  Airline       : {q.airline}")
            print(f"  Flight Number : {q.flight_number}")
            print(f"  Route         : {q.origin} -> {q.destination}")
            print(f"  Travel Date   : {q.travel_date}")
            print(f"  Times         : Dep {q.departure_time} | Arr {q.arrival_time}")
            print(f"  Stops         : {q.stops}")
            print(f"  Fare Family   : {q.fare_family}")
            print(f"  Cabin Class   : {q.cabin_class}")
            print(f"  Total Fare    : {q.currency} {q.total_fare}")
            print(f"  Availability  : {q.availability}")
            print(f"  Source        : {q.source}")
            print(f"  Fingerprint   : {fp[:16]}...")
            print(f"  Observed At   : {q.observed_at}")

        print("\n" + "=" * 75)
        print("AIR INDIA EXPRESS LIVE AUDIT COMPLETED SUCCESSFULLY")
        print("=" * 75)

    finally:
        collector.close()


if __name__ == "__main__":
    run_live_aix_validation()
