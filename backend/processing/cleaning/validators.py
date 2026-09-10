"""Quality validation rules for airfare observations.

Provides deterministic attribute and availability checks, classifying
anomalies with standardized quality status codes.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from backend.app.db.models.fare_observation import FareObservation


class QualityStatus(str, Enum):
    """Standardized observation quality statuses."""

    VALID = "VALID"
    MISSING = "MISSING"
    INVALID_FARE = "INVALID_FARE"
    SOLD_OUT = "SOLD_OUT"
    CANCELLED = "CANCELLED"
    DUPLICATE = "DUPLICATE"
    OUTLIER = "OUTLIER"
    SCRAPE_ERROR = "SCRAPE_ERROR"


@dataclass
class ValidationResult:
    """Structured result of observation validation."""

    is_valid: bool
    status: str
    reason: Optional[str] = None


def validate_observation(obs: Any) -> ValidationResult:
    """Validate observation fields and availability.

    Args:
        obs: FareObservation instance or duck-typed object.

    Returns:
        ValidationResult with is_valid boolean and standardized status.
    """
    # 1. Check for missing required relational/dimensional keys
    required_id_fields = [
        ("route_id", getattr(obs, "route_id", None)),
        ("airline_id", getattr(obs, "airline_id", None)),
        ("window_id", getattr(obs, "window_id", None)),
    ]
    for name, val in required_id_fields:
        if val is None or val <= 0:
            return ValidationResult(
                is_valid=False,
                status=QualityStatus.MISSING.value,
                reason=f"Missing or invalid required foreign key '{name}'",
            )

    # 2. Check for missing required identity fields
    if not getattr(obs, "flight_number", None) or not str(obs.flight_number).strip():
        return ValidationResult(
            is_valid=False,
            status=QualityStatus.MISSING.value,
            reason="Missing required field 'flight_number'",
        )

    if getattr(obs, "travel_date", None) is None:
        return ValidationResult(
            is_valid=False,
            status=QualityStatus.MISSING.value,
            reason="Missing required field 'travel_date'",
        )

    if getattr(obs, "observed_at", None) is None:
        return ValidationResult(
            is_valid=False,
            status=QualityStatus.MISSING.value,
            reason="Missing required field 'observed_at'",
        )

    if not getattr(obs, "cabin_class", None) or not str(obs.cabin_class).strip():
        return ValidationResult(
            is_valid=False,
            status=QualityStatus.MISSING.value,
            reason="Missing required field 'cabin_class'",
        )

    # 3. Check fare and currency validity
    total_fare = getattr(obs, "total_fare", None)
    if total_fare is None:
        return ValidationResult(
            is_valid=False,
            status=QualityStatus.INVALID_FARE.value,
            reason="Total fare cannot be None",
        )

    try:
        fare_dec = Decimal(str(total_fare))
        if fare_dec <= Decimal("0"):
            return ValidationResult(
                is_valid=False,
                status=QualityStatus.INVALID_FARE.value,
                reason=f"Non-positive total fare: {fare_dec}",
            )
    except Exception as exc:
        return ValidationResult(
            is_valid=False,
            status=QualityStatus.INVALID_FARE.value,
            reason=f"Unparseable total fare '{total_fare}': {exc}",
        )

    currency = getattr(obs, "currency", None)
    if not currency or not str(currency).strip():
        return ValidationResult(
            is_valid=False,
            status=QualityStatus.INVALID_FARE.value,
            reason="Missing or empty currency",
        )

    # 4. Check availability / sold-out status
    is_available = getattr(obs, "is_available", True)
    if is_available is False:
        return ValidationResult(
            is_valid=False,
            status=QualityStatus.SOLD_OUT.value,
            reason="Flight quotation marked as unavailable/sold out",
        )

    # Note: Scraper-tolerant nullable fields (base_fare, fuel_surcharge,
    # taxes_and_fees, convenience_fee, seats_remaining, baggage,
    # scheduled_departure_time, scheduled_arrival_time) are permitted
    # to be None and do not invalidate the observation.

    return ValidationResult(
        is_valid=True,
        status=QualityStatus.VALID.value,
        reason=None,
    )
