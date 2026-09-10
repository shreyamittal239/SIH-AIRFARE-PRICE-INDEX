"""Route-day-booking-window aggregation package."""

from backend.processing.aggregation.reconciliation import (
    CrossSourcePolicy,
    ReconciledFlight,
    get_flight_identity_key,
    reconcile_cross_source_quotes,
)
from backend.processing.aggregation.statistics import (
    RepresentativeFareMetric,
    SummaryMetrics,
    calculate_geometric_mean,
    calculate_route_summary_stats,
)
from backend.processing.aggregation.route_daily_summary_service import (
    AggregationResult,
    RouteDailySummaryService,
)

__all__ = [
    "CrossSourcePolicy",
    "ReconciledFlight",
    "get_flight_identity_key",
    "reconcile_cross_source_quotes",
    "RepresentativeFareMetric",
    "SummaryMetrics",
    "calculate_geometric_mean",
    "calculate_route_summary_stats",
    "AggregationResult",
    "RouteDailySummaryService",
]
