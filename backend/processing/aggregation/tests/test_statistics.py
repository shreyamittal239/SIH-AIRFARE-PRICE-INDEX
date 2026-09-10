"""Unit tests for statistical aggregation functions."""

from decimal import Decimal
import numpy as np
import pytest

from backend.processing.aggregation.statistics import (
    RepresentativeFareMetric,
    calculate_geometric_mean,
    calculate_route_summary_stats,
)


def test_geometric_mean_calculation():
    """Verify log-scale geometric mean calculation: GM = exp(mean(log(x)))."""
    # Sample fares: 4000, 9000
    # True GM = sqrt(4000 * 9000) = sqrt(36,000,000) = 6000.00
    fares = [Decimal("4000.00"), Decimal("9000.00")]
    gm = calculate_geometric_mean(fares)
    assert gm == Decimal("6000.00")

    # Three values: 2000, 4000, 8000
    # True GM = (2000 * 4000 * 8000)^(1/3) = (64,000,000,000)^(1/3) = 4000.00
    fares3 = [Decimal("2000.00"), Decimal("4000.00"), Decimal("8000.00")]
    assert calculate_geometric_mean(fares3) == Decimal("4000.00")


def test_geometric_mean_zero_or_negative_rejected():
    """Verify ValueError is raised if non-positive fares are passed."""
    with pytest.raises(ValueError):
        calculate_geometric_mean([Decimal("5000.00"), Decimal("0.00")])

    with pytest.raises(ValueError):
        calculate_geometric_mean([Decimal("5000.00"), Decimal("-100.00")])

    with pytest.raises(ValueError):
        calculate_geometric_mean([])


def test_calculate_route_summary_stats_all_metrics():
    """Verify calculation of min, median, mean, and geometric mean across a spread."""
    fares = [Decimal("6000.00"), Decimal("7000.00"), Decimal("8000.00")]

    # Calculate with default metric (GEOMETRIC_MEAN)
    stats_gm = calculate_route_summary_stats(fares, metric=RepresentativeFareMetric.GEOMETRIC_MEAN)
    assert stats_gm.count == 3
    assert stats_gm.min_fare == Decimal("6000.00")
    assert stats_gm.median_fare == Decimal("7000.00")
    assert stats_gm.mean_fare == Decimal("7000.00")
    # GM of 6000, 7000, 8000 = (3.36e11)^(1/3) ≈ 6952.05
    assert stats_gm.geometric_mean_fare == Decimal("6952.05")
    assert stats_gm.representative_fare == stats_gm.geometric_mean_fare

    # With MEDIAN metric
    stats_med = calculate_route_summary_stats(fares, metric=RepresentativeFareMetric.MEDIAN)
    assert stats_med.representative_fare == Decimal("7000.00")

    # With ARITHMETIC_MEAN metric
    stats_mean = calculate_route_summary_stats(fares, metric=RepresentativeFareMetric.ARITHMETIC_MEAN)
    assert stats_mean.representative_fare == Decimal("7000.00")

    # With MINIMUM metric
    stats_min = calculate_route_summary_stats(fares, metric=RepresentativeFareMetric.MINIMUM)
    assert stats_min.representative_fare == Decimal("6000.00")
