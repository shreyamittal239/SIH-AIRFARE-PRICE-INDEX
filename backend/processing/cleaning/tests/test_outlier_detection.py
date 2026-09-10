"""Unit tests for grouped statistical outlier detection."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import pytest

from backend.processing.cleaning.outlier_detection import (
    OutlierDetector,
    OutlierMethod,
)
from backend.processing.cleaning.validators import QualityStatus


def make_quote_for_outlier(
    route_id: int,
    total_fare: float,
    flight_number: str = "SG 101",
    travel_date: date = date(2026, 9, 15),
    window_id: int = 2,
    cabin_class: str = "ECONOMY",
) -> SimpleNamespace:
    """Helper to build test observations for outlier evaluation."""
    return SimpleNamespace(
        route_id=route_id,
        travel_date=travel_date,
        window_id=window_id,
        cabin_class=cabin_class,
        flight_number=flight_number,
        total_fare=Decimal(str(total_fare)),
        quality_status="VALID",
    )


def test_outlier_detected_in_cohort():
    """Verify an extreme synthetic fare is flagged as OUTLIER in an adequate sample."""
    # Cohort of 6 observations on route 1 (DEL-BOM)
    # Typical fares around ~5000-5400, plus one extreme anomaly of 55000
    fares = [5000.0, 5100.0, 5200.0, 5300.0, 5400.0, 55000.0]
    observations = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"SG {i+100}")
        for i, f in enumerate(fares)
    ]

    detector = OutlierDetector(method=OutlierMethod.IQR, iqr_multiplier=1.5, min_sample_size=5)
    result = detector.detect_outliers(observations)

    # 5 normal fares retained, 1 extreme outlier flagged
    assert len(result.clean_observations) == 5
    assert len(result.outliers) == 1
    outlier = result.outliers[0]
    assert float(outlier.total_fare) == 55000.0
    assert outlier.quality_status == QualityStatus.OUTLIER.value


def test_normal_distribution_retains_all():
    """Verify that reasonable airfare spreads do not generate false-positive outliers."""
    # Normal domestic price spread
    fares = [6500.0, 7000.0, 7500.0, 7800.0, 8200.0, 8900.0]
    observations = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"SG {i+100}")
        for i, f in enumerate(fares)
    ]

    detector = OutlierDetector(min_sample_size=5)
    result = detector.detect_outliers(observations)

    assert len(result.clean_observations) == 6
    assert len(result.outliers) == 0


def test_small_sample_does_not_create_outliers():
    """Rule: Sample size N < min_sample_size (e.g. N=3) must never flag outliers."""
    fares = [4000.0, 4500.0, 19000.0]  # Only 3 flights on this route
    observations = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"SG {i+100}")
        for i, f in enumerate(fares)
    ]

    detector = OutlierDetector(min_sample_size=5)
    result = detector.detect_outliers(observations)

    # Small sample guardrail: all 3 must be retained
    assert len(result.clean_observations) == 3
    assert len(result.outliers) == 0


def test_routes_treated_separately():
    """Verify that high-cost routes (e.g. Leh) are not compared against budget routes."""
    # Route 1 (DEL-BOM): fares around 6000
    route1_fares = [5800.0, 6000.0, 6100.0, 6200.0, 6300.0]
    obs_r1 = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"SG 10{i}")
        for i, f in enumerate(route1_fares)
    ]

    # Route 2 (DEL-IXL Leh): naturally higher base price around 18000
    route2_fares = [17000.0, 17500.0, 18000.0, 18200.0, 18500.0]
    obs_r2 = [
        make_quote_for_outlier(route_id=2, total_fare=f, flight_number=f"SG 20{i}")
        for i, f in enumerate(route2_fares)
    ]

    all_obs = obs_r1 + obs_r2
    detector = OutlierDetector(min_sample_size=5)
    result = detector.detect_outliers(all_obs)

    # Neither route cohort should falsely flag the other route's price levels
    assert len(result.clean_observations) == 10
    assert len(result.outliers) == 0


def test_mad_outlier_detection_method():
    """Verify MAD (Median Absolute Deviation) method correctly flags extreme outliers."""
    fares = [7000.0, 7100.0, 7050.0, 7200.0, 7150.0, 48000.0]
    observations = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"SG {i}")
        for i, f in enumerate(fares)
    ]

    detector = OutlierDetector(method=OutlierMethod.MAD, mad_threshold=3.0, min_sample_size=5)
    result = detector.detect_outliers(observations)

    assert len(result.clean_observations) == 5
    assert len(result.outliers) == 1
    assert float(result.outliers[0].total_fare) == 48000.0


def test_exact_yatra_low_dispersion_regression():
    """TEST 1 — Exact Yatra regression.

    Simulates the Yatra CollectionRun #76 cohort:
    - 22 flights at ₹6,530.00
    - 2 flights at ₹6,490.00
    Verifies that IQR = 0 enters low-dispersion handling, and ₹6,490 is NOT flagged as OUTLIER.
    """
    fares = [6490.0, 6490.0] + [6530.0] * 22
    observations = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"FL {i}")
        for i, f in enumerate(fares)
    ]

    detector = OutlierDetector(method=OutlierMethod.IQR, iqr_multiplier=1.5, min_sample_size=5)
    result = detector.detect_outliers(observations)

    # All 24 flights are retained as valid clean observations
    assert len(result.clean_observations) == 24
    assert len(result.outliers) == 0
    for o in result.clean_observations:
        assert o.quality_status == QualityStatus.VALID.value

    # Verify cohort stats entered low-dispersion mode
    cohort_key = (1, "2026-09-15", 2, "ECONOMY")
    stats = result.cohort_stats[cohort_key]
    assert stats.q1 == 6530.0
    assert stats.q3 == 6530.0
    assert stats.is_low_dispersion is True
    # Default tolerance: max(500.0, 6530.0 * 0.10) = 653.0 -> [5877.0, 7183.0]
    assert stats.lower_bound == 5877.0
    assert stats.upper_bound == 7183.0


def test_all_values_identical_cohort():
    """TEST 2 — All values identical cohort.

    Input: [6530] * 24.
    Expected: IQR = 0, is_low_dispersion is True, 0 outliers flagged.
    """
    fares = [6530.0] * 24
    observations = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"FL {i}")
        for i, f in enumerate(fares)
    ]

    detector = OutlierDetector()
    result = detector.detect_outliers(observations)

    assert len(result.clean_observations) == 24
    assert len(result.outliers) == 0
    cohort_key = (1, "2026-09-15", 2, "ECONOMY")
    assert result.cohort_stats[cohort_key].is_low_dispersion is True


def test_low_dispersion_with_genuine_extreme_value():
    """TEST 3 — Low-dispersion cohort containing a genuinely extreme value.

    Input: [6530] * 22 + [6490, 50000].
    Expected:
    - ₹50,000 is classified as OUTLIER
    - ₹6,490 is NOT classified as OUTLIER
    """
    fares = [6490.0] + [6530.0] * 22 + [50000.0]
    observations = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"FL {i}")
        for i, f in enumerate(fares)
    ]

    detector = OutlierDetector(min_sample_size=5)
    result = detector.detect_outliers(observations)

    # 23 clean (22 at 6530 + 1 at 6490), 1 outlier (50000)
    assert len(result.clean_observations) == 23
    assert len(result.outliers) == 1
    outlier = result.outliers[0]
    assert float(outlier.total_fare) == 50000.0
    assert outlier.quality_status == QualityStatus.OUTLIER.value

    # Confirm ₹6,490 is in clean_observations
    clean_fares = [float(o.total_fare) for o in result.clean_observations]
    assert 6490.0 in clean_fares
    assert 50000.0 not in clean_fares


def test_zero_mad_fallback_safe():
    """TEST 6 — Zero MAD fallback.

    Verify that when MAD = 0 (due to identical values), the detector does not
    cause division-by-zero, NaN, or universal outlier classification.
    """
    fares = [6490.0, 6490.0] + [6530.0] * 22
    observations = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"FL {i}")
        for i, f in enumerate(fares)
    ]

    detector_mad = OutlierDetector(method=OutlierMethod.MAD, mad_threshold=3.0)
    result = detector_mad.detect_outliers(observations)

    assert len(result.clean_observations) == 24
    assert len(result.outliers) == 0


def test_low_dispersion_tolerance_configurability():
    """TEST 7 — Configurability of low-dispersion tolerances.

    Verify that changing configured tolerances changes low-dispersion bounds deterministically:
    - strict/tiny tolerance flags ₹6,490 (deviation 40 > absolute tolerance 10)
    - standard/wider tolerance retains ₹6,490
    """
    fares = [6490.0, 6490.0] + [6530.0] * 22
    obs_strict = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"FL {i}")
        for i, f in enumerate(fares)
    ]

    # Strict detector: max(10.0, 6530 * 0.001 = 6.53) = 10.0 -> lower bound = 6520.0
    strict_detector = OutlierDetector(
        low_dispersion_relative_tolerance=0.001,
        low_dispersion_absolute_tolerance=10.0,
    )
    res_strict = strict_detector.detect_outliers(obs_strict)
    assert len(res_strict.clean_observations) == 22
    assert len(res_strict.outliers) == 2
    for o in res_strict.outliers:
        assert float(o.total_fare) == 6490.0

    # Permissive detector: max(500.0, 6530 * 0.10 = 653.0) = 653.0 -> lower bound = 5877.0
    obs_permissive = [
        make_quote_for_outlier(route_id=1, total_fare=f, flight_number=f"FL {i}")
        for i, f in enumerate(fares)
    ]
    permissive_detector = OutlierDetector(
        low_dispersion_relative_tolerance=0.10,
        low_dispersion_absolute_tolerance=500.0,
    )
    res_permissive = permissive_detector.detect_outliers(obs_permissive)
    assert len(res_permissive.clean_observations) == 24
    assert len(res_permissive.outliers) == 0


