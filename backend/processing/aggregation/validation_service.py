"""Stage 6: Index Validation Service.

Validates the computed National Composite Index against DGCA published benchmarks
using Mean Absolute Percentage Error (MAPE).
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import List, Optional, Dict, Tuple
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from backend.app.db.models.index_daily import IndexDaily
from backend.app.db.models.dgca_fare_benchmark import DGCAFareBenchmark

logger = logging.getLogger(__name__)

@dataclass
class PeriodError:
    period: date
    our_value: Decimal
    dgca_value: Decimal
    abs_percentage_error: Decimal

@dataclass
class ValidationReport:
    period_type: str  # 'MONTHLY' or 'YEARLY'
    base_period_code: str
    mape: Optional[Decimal] = None
    is_passed: Optional[bool] = None
    threshold: float = 5.0
    matched_periods: List[PeriodError] = field(default_factory=list)
    unmatched_periods: List[date] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def __str__(self) -> str:
        status = "PASS" if self.is_passed else "FAIL" if self.is_passed is not None else "N/A"
        res = [
            f"--- Validation Report ({self.period_type}) ---",
            f"Period Code: {self.base_period_code}",
            f"Threshold: {self.threshold}%",
            f"MAPE: {self.mape:.4f}%" if self.mape is not None else "MAPE: N/A",
            f"Verdict: {status}",
            f"\nMatched Periods ({len(self.matched_periods)}):"
        ]
        for p in self.matched_periods:
            res.append(f"  {p.period}: Our={p.our_value:.4f}, DGCA={p.dgca_value:.4f}, Error={p.abs_percentage_error:.4f}%")

        if self.unmatched_periods:
            res.append(f"\nUnmatched Periods ({len(self.unmatched_periods)}):")
            for u in self.unmatched_periods:
                res.append(f"  {u}")

        return "\n".join(res)

class ValidationService:
    """Orchestrates Stage 6: Validating computed indices against external benchmarks."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def validate_monthly(self, base_period_code: str, threshold: float = 5.0) -> ValidationReport:
        """
        Validates monthly national composite indices against DGCA monthly benchmarks.
        """
        logger.info("Validating monthly indices for period %s", base_period_code)

        # 1. Fetch monthly national composite indices
        stmt = select(IndexDaily).where(
            IndexDaily.index_level == "NATIONAL_COMPOSITE",
            IndexDaily.period_type == "MONTHLY",
            IndexDaily.base_period_code == base_period_code,
            IndexDaily.route_id == None,
            IndexDaily.window_id == None
        ).order_by(IndexDaily.index_date)

        our_records = self.db.scalars(stmt).all()

        if not our_records:
            logger.warning("No monthly national indices found for %s", base_period_code)
            return ValidationReport(period_type="MONTHLY", base_period_code=base_period_code, threshold=threshold)

        matched = []
        unmatched = []

        for rec in our_records:
            # Match by index_date (which is 1st of month) to dgca_fare_benchmark.period_start
            # Only for national benchmarks (route_id is NULL)
            benchmark_stmt = select(DGCAFareBenchmark).where(
                DGCAFareBenchmark.route_id == None,
                DGCAFareBenchmark.period_start == rec.index_date
            )
            dgca_rec = self.db.execute(benchmark_stmt).scalar_one_or_none()

            if dgca_rec:
                dgca_val = dgca_rec.dgca_avg_fare
                error = self._calculate_abs_percentage_error(rec.index_value, dgca_val)
                matched.append(PeriodError(
                    period=rec.index_date,
                    our_value=rec.index_value,
                    dgca_value=dgca_val,
                    abs_percentage_error=error
                ))
            else:
                unmatched.append(rec.index_date)

        mape = self._calculate_mape(matched)
        is_passed = (mape <= Decimal(str(threshold))) if mape is not None else None

        return ValidationReport(
            period_type="MONTHLY",
            base_period_code=base_period_code,
            mape=mape,
            is_passed=is_passed,
            threshold=threshold,
            matched_periods=matched,
            unmatched_periods=unmatched
        )

    def validate_yearly(self, base_period_code: str, threshold: float = 5.0) -> ValidationReport:
        """
        Validates yearly national composite indices against average of DGCA monthly benchmarks.
        """
        logger.info("Validating yearly indices for period %s", base_period_code)

        # 1. Fetch yearly national composite indices
        stmt = select(IndexDaily).where(
            IndexDaily.index_level == "NATIONAL_COMPOSITE",
            IndexDaily.period_type == "YEARLY",
            IndexDaily.base_period_code == base_period_code,
            IndexDaily.route_id == None,
            IndexDaily.window_id == None
        ).order_by(IndexDaily.index_date)

        our_records = self.db.scalars(stmt).all()

        if not our_records:
            logger.warning("No yearly national indices found for %s", base_period_code)
            return ValidationReport(period_type="YEARLY", base_period_code=base_period_code, threshold=threshold)

        matched = []
        unmatched = []

        for rec in our_records:
            year = rec.index_date.year

            # Match: Average of all DGCA benchmarks for that calendar year
            benchmark_stmt = select(DGCAFareBenchmark.dgca_avg_fare).where(
                DGCAFareBenchmark.route_id == None,
                func.extract('year', DGCAFareBenchmark.period_start) == year
            )
            results = self.db.execute(benchmark_stmt).scalars().all()

            if results:
                dgca_val = sum(results) / Decimal(len(results))
                dgca_val = dgca_val.quantize(Decimal("1.0000"))

                error = self._calculate_abs_percentage_error(rec.index_value, dgca_val)
                matched.append(PeriodError(
                    period=rec.index_date,
                    our_value=rec.index_value,
                    dgca_value=dgca_val,
                    abs_percentage_error=error
                ))
                # Metadata: number of months used for the DGCA average
                # We'll store this in the report metadata as a map {year: count}
            else:
                unmatched.append(rec.index_date)

        mape = self._calculate_mape(matched)
        is_passed = (mape <= Decimal(str(threshold))) if mape is not None else None

        return ValidationReport(
            period_type="YEARLY",
            base_period_code=base_period_code,
            mape=mape,
            is_passed=is_passed,
            threshold=threshold,
            matched_periods=matched,
            unmatched_periods=unmatched
        )

    def _calculate_abs_percentage_error(self, our_val: Decimal, dgca_val: Decimal) -> Decimal:
        """|our - dgca| / dgca * 100"""
        if dgca_val == 0:
            return Decimal("0.0000")
        error = abs(our_val - dgca_val) / dgca_val * 100
        return error.quantize(Decimal("1.0000"))

    def _calculate_mape(self, errors: List[PeriodError]) -> Optional[Decimal]:
        """Mean Absolute Percentage Error"""
        if not errors:
            return None
        total_error = sum(p.abs_percentage_error for p in errors)
        mape = total_error / Decimal(len(errors))
        return mape.quantize(Decimal("1.0000"))
