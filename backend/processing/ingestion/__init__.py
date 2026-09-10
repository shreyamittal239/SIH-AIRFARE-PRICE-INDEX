"""Data ingestion package."""

from backend.processing.ingestion.fingerprint import (
    compute_flight_fingerprint,
    get_canonical_fingerprint_string,
    normalize_flight_number,
)
from backend.processing.ingestion.resolver import (
    DimensionResolver,
    ResolvedDimensions,
)
from backend.processing.ingestion.fare_mapper import map_quote_to_observation
from backend.processing.ingestion.repository import (
    IngestionRepository,
    IngestionResult,
)

__all__ = [
    "compute_flight_fingerprint",
    "get_canonical_fingerprint_string",
    "normalize_flight_number",
    "DimensionResolver",
    "ResolvedDimensions",
    "map_quote_to_observation",
    "IngestionRepository",
    "IngestionResult",
]
