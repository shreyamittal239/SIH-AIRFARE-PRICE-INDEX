"""Statistical metrics computation for route-window aggregations.

Implements minimum, median, arithmetic mean, and log-scale geometric mean
with numerical stability and configurable representative fare metric selection.

Terminology Note:
The geometric mean of fare levels (geometric_mean_fare) is a representative-fare
level statistic for a flight cohort within a given period. It should not be confused
with a Jevons price index, which is constructed from geometric means of price relatives
(P_t / P_0) across distinct time periods. In this prototype, geometric_mean_fare serves
as a valid candidate representative-fare metric.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import List, Union
import numpy as np


class RepresentativeFareMetric(str, Enum):
    """Available representative fare selection methodologies."""

    GEOMETRIC_MEAN = "GEOMETRIC_MEAN"
    MEDIAN = "MEDIAN"
    ARITHMETIC_MEAN = "ARITHMETIC_MEAN"
    MINIMUM = "MINIMUM"


@dataclass
class SummaryMetrics:
    """Statistical summary metrics for a route-window daily cohort."""

    count: int
    min_fare: Decimal
    median_fare: Decimal
    mean_fare: Decimal
    geometric_mean_fare: Decimal
    representative_fare: Decimal


def calculate_geometric_mean(fares: List[Union[Decimal, float]]) -> Decimal:
    """Compute the geometric mean using numerically stable log-sum-exp.

    GM = exp(mean(log(x)))
    Requires all fares to be strictly positive (> 0).
    """
    if not fares:
        raise ValueError("Cannot compute geometric mean on an empty fare list.")

    float_fares = [float(f) for f in fares]
    if any(f <= 0 for f in float_fares):
        raise ValueError("Geometric mean is only defined for strictly positive fares.")

    log_fares = np.log(float_fares)
    gm = float(np.exp(np.mean(log_fares)))
    return Decimal(str(round(gm, 2)))


def calculate_route_summary_stats(
    fares: List[Union[Decimal, float]],
    metric: RepresentativeFareMetric = RepresentativeFareMetric.GEOMETRIC_MEAN,
) -> SummaryMetrics:
    """Calculate all standard summary statistics and select the representative fare.

    Args:
        fares: List of comparable flight-level fares.
        metric: Metric to designate as representative_fare. Defaults to GEOMETRIC_MEAN.

    Returns:
        SummaryMetrics dataclass containing count, min, median, mean, geometric mean,
        and the designated representative fare.
    """
    if not fares:
        raise ValueError("Cannot calculate summary statistics for empty fares list.")

    float_fares = [float(f) for f in fares]

    min_val = Decimal(str(round(float(np.min(float_fares)), 2)))
    med_val = Decimal(str(round(float(np.median(float_fares)), 2)))
    mean_val = Decimal(str(round(float(np.mean(float_fares)), 2)))
    gm_val = calculate_geometric_mean(fares)

    # Select representative fare according to configured policy
    if metric == RepresentativeFareMetric.GEOMETRIC_MEAN:
        rep_val = gm_val
    elif metric == RepresentativeFareMetric.MEDIAN:
        rep_val = med_val
    elif metric == RepresentativeFareMetric.ARITHMETIC_MEAN:
        rep_val = mean_val
    elif metric == RepresentativeFareMetric.MINIMUM:
        rep_val = min_val
    else:
        rep_val = gm_val

    return SummaryMetrics(
        count=len(fares),
        min_fare=min_val,
        median_fare=med_val,
        mean_fare=mean_val,
        geometric_mean_fare=gm_val,
        representative_fare=rep_val,
    )
