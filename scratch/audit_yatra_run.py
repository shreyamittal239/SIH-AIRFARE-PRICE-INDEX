"""Audit script for CollectionRun #76 and Yatra observations."""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from datetime import date, timedelta
from decimal import Decimal
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from backend.app.db.database import SessionLocal
from backend.app.db.models.collection_run import CollectionRun
from backend.app.db.models.fare_observation import FareObservation
from backend.app.db.models.airline import Airline
from backend.app.db.models.route import Route
from backend.app.db.models.data_source import DataSource
from backend.processing.cleaning.cleaning_pipeline import CleaningPipeline
from backend.processing.cleaning.outlier_detection import OutlierDetector
from backend.processing.aggregation.route_daily_summary_service import RouteDailySummaryService
from backend.processing.aggregation.reconciliation import reconcile_cross_source_quotes, CrossSourcePolicy


def run_audit():
    db = SessionLocal()
    try:
        # 1. Fetch CollectionRun #76
        run = db.get(CollectionRun, 76)
        print(f"CollectionRun: run_id={run.run_id}, source_id={run.source_id}, status={run.status}, scraped={run.records_scraped}")

        # Fetch all observations for run 76
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
        print(f"Found {len(obs_list)} FareObservation records in Run #76\n")

        print("=" * 130)
        print(f"{'ObsID':<6} | {'Airline':<18} | {'Flight#':<12} | {'Route':<9} | {'TravelDate':<10} | {'Dep':<5} | {'Arr':<5} | {'Fare':<8} | {'Stops':<5} | {'Status':<8} | {'Fingerprint'}")
        print("-" * 130)
        for o in obs_list:
            airline_name = o.airline.airline_name if o.airline else "Unknown"
            route_str = f"{o.route.origin_city_id}->{o.route.destination_city_id}" if o.route else str(o.route_id)
            dep_str = o.scheduled_departure_time.strftime("%H:%M") if o.scheduled_departure_time else "N/A"
            arr_str = o.scheduled_arrival_time.strftime("%H:%M") if o.scheduled_arrival_time else "N/A"
            fare_str = f"{o.total_fare:.2f}" if o.total_fare else "N/A"
            fp_short = (o.fingerprint_hash[:16] + "...") if o.fingerprint_hash else "None"
            print(f"{o.observation_id:<6} | {airline_name:<18} | {o.flight_number:<12} | {route_str:<9} | {str(o.travel_date):<10} | {dep_str:<5} | {arr_str:<5} | {fare_str:<8} | {o.stops:<5} | {o.quality_status:<8} | {fp_short}")

        # 2. Outlier Analysis on the 24 observations
        print("\n" + "=" * 80)
        print("OUTLIER DETECTOR DEEP DIVE")
        print("=" * 80)

        detector = OutlierDetector()
        fares = [float(o.total_fare) for o in obs_list]
        print(f"Fare values ({len(fares)} total): {sorted(fares)}")
        
        # Breakdown of values
        from collections import Counter
        fare_counts = Counter(fares)
        print("Fare distribution:")
        for f, cnt in sorted(fare_counts.items()):
            print(f"  ₹{f:.2f}: {cnt} observations")

        # Let's run detector.detect_outliers on this cohort
        outlier_res = detector.detect_outliers(obs_list)
        print(f"\nTotal Outliers Flagged by detector: {len(outlier_res.outliers)}")
        for o in outlier_res.outliers:
            print(f"  - Obs ID {o.observation_id} ({o.flight_number}): fare=₹{o.total_fare}")

        # Mathematical calculations
        fares_arr = np.array(fares)
        q1 = np.percentile(fares_arr, 25)
        q3 = np.percentile(fares_arr, 75)
        iqr = q3 - q1
        iqr_lower = q1 - 1.5 * iqr
        iqr_upper = q3 + 1.5 * iqr

        median = np.median(fares_arr)
        mad = np.median(np.abs(fares_arr - median))
        
        print("\n[IQR Mathematics]")
        print(f"  Q1 (25th percentile): {q1:.2f}")
        print(f"  Q3 (75th percentile): {q3:.2f}")
        print(f"  IQR (Q3 - Q1):        {iqr:.2f}")
        print(f"  Lower Bound (Q1 - 1.5*IQR): {iqr_lower:.2f}")
        print(f"  Upper Bound (Q3 + 1.5*IQR): {iqr_upper:.2f}")

        print("\n[MAD Mathematics]")
        print(f"  Median:               {median:.2f}")
        print(f"  Absolute deviations:  {np.abs(fares_arr - median)}")
        print(f"  MAD (median abs dev): {mad:.2f}")
        if mad > 0:
            mod_z = 0.6745 * (fares_arr - median) / mad
            print(f"  Modified Z-scores:    min={np.min(mod_z):.4f}, max={np.max(mod_z):.4f}")
        else:
            print("  MAD is 0.00! Modified Z-score cannot be evaluated normally because MAD = 0.")

        # 3. Flight Uniqueness Check
        print("\n" + "=" * 80)
        print("FLIGHT IDENTITY & PHYSICAL UNIQUENESS")
        print("=" * 80)
        keys = []
        for o in obs_list:
            key = (o.airline_id, o.flight_number, o.route_id, o.travel_date, o.scheduled_departure_time, o.cabin_class)
            keys.append(key)
        
        unique_keys = set(keys)
        print(f"Total Observations: {len(keys)}")
        print(f"Unique Physical Flight Keys: {len(unique_keys)}")
        if len(keys) == len(unique_keys):
            print("CONFIRMED: All 24 observations represent distinct physical flights.")
        else:
            print("WARNING: Duplicate physical flight keys detected!")

        # 4. Outlier Inclusion / Exclusion Semantics
        print("\n" + "=" * 80)
        print("OUTLIER SEMANTICS IN ROUTEDAILYSUMMARYSERVICE")
        print("=" * 80)
        # Service with exclude_outliers=True
        svc_exclude = RouteDailySummaryService(db, exclude_outliers=True)
        res_exclude = svc_exclude.aggregate_cohorts(obs_list)
        print(f"With exclude_outliers=True:")
        if res_exclude:
            s = res_exclude[0]
            print(f"  Eligible count: {s.observations_count}")
            print(f"  Min fare:       ₹{s.min_fare}")
            print(f"  Representative: ₹{s.representative_fare}")
        
        # Service with exclude_outliers=False
        svc_include = RouteDailySummaryService(db, exclude_outliers=False)
        res_include = svc_include.aggregate_cohorts(obs_list)
        print(f"\nWith exclude_outliers=False:")
        if res_include:
            s = res_include[0]
            print(f"  Eligible count: {s.observations_count}")
            print(f"  Min fare:       ₹{s.min_fare}")
            print(f"  Median fare:    ₹{s.median_fare}")
            print(f"  Mean fare:      ₹{s.mean_fare}")
            print(f"  Geometric Mean: ₹{s.geometric_mean_fare}")
            print(f"  Representative: ₹{s.representative_fare}")

        # 5. Check Cross-Source Overlap with SpiceJet Direct
        print("\n" + "=" * 80)
        print("CROSS-SOURCE OVERLAP CHECK: SPICEJET DIRECT vs YATRA")
        print("=" * 80)
        # Find SpiceJet direct observations for DEL -> BOM, travel_date=2026-09-15
        stmt_sj = (
            select(FareObservation)
            .where(
                FareObservation.travel_date == date(2026, 9, 15),
            )
            .options(
                joinedload(FareObservation.airline),
                joinedload(FareObservation.data_source),
            )
        )
        all_obs = db.scalars(stmt_sj).unique().all()
        sj_direct = [o for o in all_obs if "SPICEJET" in (o.data_source.source_code if o.data_source else "")]
        yatra_sj = [o for o in obs_list if "SPICEJET" in (o.airline.airline_name.upper() if o.airline else "")]

        print(f"SpiceJet Direct observations on 2026-09-15: {len(sj_direct)}")
        for o in sj_direct:
            print(f"  Direct: run_id={o.run_id}, flight={o.flight_number}, dep={o.scheduled_departure_time}, fare=₹{o.total_fare}")

        print(f"Yatra SpiceJet observations on 2026-09-15: {len(yatra_sj)}")
        for o in yatra_sj:
            print(f"  Yatra:  run_id={o.run_id}, flight={o.flight_number}, dep={o.scheduled_departure_time}, fare=₹{o.total_fare}")

        # Check overlap
        overlap_flights = set(o.flight_number for o in sj_direct).intersection(set(o.flight_number for o in yatra_sj))
        print(f"\nOverlapping flight numbers: {overlap_flights}")

        # Also check all SpiceJet direct observations in DB regardless of travel date
        stmt_all_sj = select(FareObservation).join(DataSource).where(DataSource.source_code.ilike("%SPICE%"))
        all_sj = db.scalars(stmt_all_sj).all()
        print(f"\nAll SpiceJet Direct observations in DB: {len(all_sj)}")
        for o in all_sj:
            print(f"  Direct Obs ID {o.observation_id}: flight={o.flight_number}, travel_date={o.travel_date}, dep={o.scheduled_departure_time}, fare=₹{o.total_fare}")

    finally:
        db.close()


if __name__ == "__main__":
    run_audit()
