"""Verification script for Step 5.2 low-dispersion fix on CollectionRun #76."""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from backend.app.db.database import SessionLocal
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.processing.cleaning.cleaning_pipeline import CleaningPipeline
from backend.processing.cleaning.outlier_detection import OutlierDetector
from backend.processing.aggregation.route_daily_summary_service import RouteDailySummaryService


def run_check():
    db = SessionLocal()
    try:
        # 1. Load CollectionRun #76 from PostgreSQL
        run = db.get(CollectionRun, 76)
        print("=" * 80)
        print("STEP 5.2 — COLLECTION RUN #76 VERIFICATION")
        print(f"Run ID: {run.run_id}, Source ID: {run.source_id}, Status: {run.status}")
        print("=" * 80)

        stmt = (
            select(FareObservation)
            .where(FareObservation.run_id == 76)
            .options(
                joinedload(FareObservation.airline),
                joinedload(FareObservation.route),
                joinedload(FareObservation.data_source),
                joinedload(FareObservation.booking_window),
            )
            .order_by(FareObservation.observation_id)
        )
        obs_list = db.scalars(stmt).unique().all()
        print(f"Total raw FareObservation records loaded from DB: {len(obs_list)}")

        # 2. Execute cleaning pipeline with default OutlierDetector (now including low-dispersion tolerance)
        pipeline = CleaningPipeline()
        summary = pipeline.clean(obs_list)

        print("\n[CLEANING PIPELINE SUMMARY]")
        print(f"  Total Evaluated: {summary.total}")
        print(f"  Valid:           {summary.valid}")
        print(f"  Outliers:        {summary.outlier}")
        print(f"  Missing:         {summary.missing}")
        print(f"  Invalid Fare:    {summary.invalid_fare}")
        print(f"  Sold Out:        {summary.sold_out}")
        print(f"  Duplicate:       {summary.duplicate}")

        outlier_obs_ids = [obs.observation_id for obs, status, _ in summary.flagged_observations if status == "OUTLIER"]
        print(f"  Outlier Obs IDs: {outlier_obs_ids}")

        # Check status of Obs 144 and 145 (the ₹6,490 flights)
        obs_144 = next((o for o in obs_list if o.observation_id == 144), None)
        obs_145 = next((o for o in obs_list if o.observation_id == 145), None)
        print("\n[INSPECTION OF PREVIOUSLY FLAGGED OBSERVATIONS]")
        if obs_144:
            print(f"  Obs 144 ({obs_144.flight_number}, fare=INR {obs_144.total_fare}): quality_status = '{obs_144.quality_status}'")
        if obs_145:
            print(f"  Obs 145 ({obs_145.flight_number}, fare=INR {obs_145.total_fare}): quality_status = '{obs_145.quality_status}'")

        # 3. Check Aggregation with exclude_outliers=True
        print("\n" + "=" * 80)
        print("AGGREGATION BEHAVIOR CHECK")
        print("=" * 80)
        svc_excl = RouteDailySummaryService(db, exclude_outliers=True)
        summaries_excl = svc_excl.aggregate_cohorts(obs_list)
        print("1. With exclude_outliers=True:")
        if summaries_excl:
            s = summaries_excl[0]
            print(f"  Observations Count:  {s.observations_count} (out of {len(obs_list)})")
            print(f"  Min Fare:            INR {s.min_fare}")
            print(f"  Median Fare:         INR {s.median_fare}")
            print(f"  Mean Fare:           INR {s.mean_fare}")
            print(f"  Geometric Mean Fare: INR {s.geometric_mean_fare}")
            print(f"  Representative Fare: INR {s.representative_fare}")

        # 4. Check Aggregation with exclude_outliers=False
        svc_incl = RouteDailySummaryService(db, exclude_outliers=False)
        summaries_incl = svc_incl.aggregate_cohorts(obs_list)
        print("\n2. With exclude_outliers=False:")
        if summaries_incl:
            s = summaries_incl[0]
            print(f"  Observations Count:  {s.observations_count} (out of {len(obs_list)})")
            print(f"  Min Fare:            INR {s.min_fare}")
            print(f"  Median Fare:         INR {s.median_fare}")
            print(f"  Mean Fare:           INR {s.mean_fare}")
            print(f"  Geometric Mean Fare: INR {s.geometric_mean_fare}")
            print(f"  Representative Fare: INR {s.representative_fare}")

        # Verify whether the numbers match
        if summaries_excl and summaries_incl:
            s_excl = summaries_excl[0]
            s_incl = summaries_incl[0]
            if s_excl.observations_count == s_incl.observations_count == 24 and s_excl.min_fare == Decimal("6490.00"):
                print("\n[SUCCESS] The ₹6,490 observations are retained and included in RouteDailySummary aggregation under BOTH exclude_outliers=True and exclude_outliers=False!")

    finally:
        db.close()


if __name__ == "__main__":
    run_check()
