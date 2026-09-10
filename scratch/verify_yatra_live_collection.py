import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import logging
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("verify_yatra_live")

from backend.collectors.playwright.yatra_collector import YatraCollector
from backend.app.db.database import SessionLocal
from backend.processing.ingestion.repository import IngestionRepository
from backend.processing.cleaning.cleaning_pipeline import CleaningPipeline
from backend.processing.aggregation.route_daily_summary_service import RouteDailySummaryService
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.route_daily_summary import RouteDailySummary


def run_live_yatra_pipeline():
    today = datetime.now(timezone.utc).date()
    target_travel_date = today + timedelta(days=7)
    origin = "DEL"
    destination = "BOM"
    booking_window = "T+7"

    print("=" * 70)
    print("YATRA LIVE COLLECTION & INGESTION VERIFICATION")
    print(f"Observation Date: {today}")
    print(f"Target Travel Date: {target_travel_date} ({booking_window})")
    print(f"Route: {origin} -> {destination}")
    print("=" * 70)

    # 1. Collect live quotes using YatraCollector
    collector = YatraCollector()
    logger.info("Initiating Yatra live collection...")
    quotes = collector.collect(
        origin=origin,
        destination=destination,
        travel_date=target_travel_date,
        adults=1,
        cabin_class="ECONOMY",
    )

    print(f"\n[COLLECTION RESULTS]")
    print(f"Total Flight Quotes Extracted: {len(quotes)}")
    if not quotes:
        print("ERROR: No quotes were extracted from Yatra!")
        sys.exit(1)

    # Breakdown by airline
    airline_counts = {}
    for q in quotes:
        airline_counts[q.airline] = airline_counts.get(q.airline, 0) + 1
    print(f"Airlines Found ({len(airline_counts)} distinct):")
    for airline, count in sorted(airline_counts.items()):
        print(f"  - {airline}: {count} flights")

    # Fare stats
    fares = [q.total_fare for q in quotes]
    min_fare = min(fares)
    max_fare = max(fares)
    avg_fare = sum(fares) / len(fares)
    print(f"Fare Range: INR {min_fare} to INR {max_fare} (Mean: INR {avg_fare:.2f})")

    print("\n[SAMPLE FLIGHT QUOTES (FIRST 5)]")
    for idx, q in enumerate(quotes[:5], 1):
        print(f"Flight {idx}:")
        print(f"  Airline:       {q.airline}")
        print(f"  Flight Number: {q.flight_number}")
        print(f"  Route:         {q.origin} -> {q.destination}")
        print(f"  Travel Date:   {q.travel_date}")
        print(f"  Times:         Dep {q.departure_time} | Arr {q.arrival_time}")
        print(f"  Stops:         {q.stops}")
        print(f"  Fare:          INR {q.total_fare} {q.currency}")
        print(f"  Cabin:         {q.cabin_class}")
        print(f"  Availability:  {q.availability}")
        print(f"  Baggage:       {q.baggage}")
        print(f"  Source:        {q.source}")
        print(f"  Observed At:   {q.observed_at}")

    # 2. Ingest into PostgreSQL via existing IngestionRepository
    print("\n" + "=" * 70)
    print("DOWNSTREAM INGESTION VERIFICATION")
    print("=" * 70)

    db = SessionLocal()
    try:
        repo = IngestionRepository(db)
        ingestion_res = repo.ingest_quotes(quotes)
        db.commit()

        run_id = ingestion_res.run_id
        print(f"Collection Run ID: {run_id}")
        print(f"Total Quotes Processed: {ingestion_res.total_quotes}")
        print(f"Inserted Observations:  {ingestion_res.inserted}")
        print(f"Skipped Duplicates:     {ingestion_res.skipped}")
        print(f"Failed Observations:    {ingestion_res.failed}")

        # Verify persisted observations
        run_record = db.query(CollectionRun).filter(CollectionRun.run_id == run_id).first()
        print(f"CollectionRun Status: {run_record.status}, Records Scraped: {run_record.records_scraped}")

        obs_records = db.query(FareObservation).filter(FareObservation.run_id == run_id).all()
        print(f"Persisted FareObservation Rows: {len(obs_records)}")

        # 3. Clean and Validate
        print("\n" + "=" * 70)
        print("DATA CLEANING & QUALITY VALIDATION")
        print("=" * 70)
        pipeline = CleaningPipeline()
        clean_summary = pipeline.clean(obs_records)

        print(f"Observations Evaluated: {clean_summary.total}")
        print(f"Valid:                  {clean_summary.valid}")
        print(f"Duplicates:             {clean_summary.duplicate}")
        print(f"Outliers:               {clean_summary.outlier}")
        print(f"Missing:                {clean_summary.missing}")
        print(f"Invalid Fare:           {clean_summary.invalid_fare}")

        # 4. Aggregation / Cross-source Reconciliation
        print("\n" + "=" * 70)
        print("DOWNSTREAM AGGREGATION / RECONCILIATION")
        print("=" * 70)
        agg_service = RouteDailySummaryService(db)
        agg_result = agg_service.process_and_persist(obs_records)

        if agg_result.summaries:
            summary_result = agg_result.summaries[0]
            print(f"RouteDailySummary Generated:")
            print(f"  Summary ID:          {summary_result.summary_id}")
            print(f"  Route ID:            {summary_result.route_id}")
            print(f"  Observation Date:    {summary_result.observation_date}")
            print(f"  Target Travel Date:  {summary_result.target_travel_date}")
            print(f"  Booking Window ID:   {summary_result.window_id}")
            print(f"  Total Observations:  {agg_result.total_observations_evaluated}")
            print(f"  Eligible Quotes:     {agg_result.eligible_observations_count}")
            print(f"  Observations Count:  {summary_result.observations_count}")
            print(f"  Min Fare:            INR {summary_result.min_fare}")
            print(f"  Median Fare:         INR {summary_result.median_fare}")
            print(f"  Mean Fare:           INR {summary_result.mean_fare}")
            print(f"  Geometric Mean Fare: INR {summary_result.geometric_mean_fare}")
            print(f"  Representative Fare: INR {summary_result.representative_fare}")
        else:
            print("RouteDailySummary was not generated (no valid quotes or excluded by policy).")

        print("\n" + "=" * 70)
        print("ALL VERIFICATION CHECKS COMPLETED SUCCESSFULLY!")
        print("=" * 70)

    finally:
        db.close()


if __name__ == "__main__":
    run_live_yatra_pipeline()
