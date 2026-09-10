"""Unit tests for cross-source flight quotation reconciliation."""

from datetime import date, time
from decimal import Decimal
from types import SimpleNamespace
import pytest

from backend.processing.aggregation.reconciliation import (
    CrossSourcePolicy,
    get_flight_identity_key,
    reconcile_cross_source_quotes,
)


def make_quote_for_reconciliation(
    flight_number: str,
    total_fare: float,
    data_source_id: int,
    source_type: str = "OTA",
    observation_id: int = 1,
    airline_id: int = 1,
    route_id: int = 1,
    travel_date: date = date(2026, 9, 15),
    departure_time: time = time(10, 0),
    cabin_class: str = "ECONOMY",
) -> SimpleNamespace:
    """Create a mock observation for cross-source reconciliation."""
    return SimpleNamespace(
        observation_id=observation_id,
        flight_number=flight_number,
        total_fare=Decimal(str(total_fare)),
        data_source_id=data_source_id,
        data_source=SimpleNamespace(source_type=source_type),
        airline_id=airline_id,
        route_id=route_id,
        travel_date=travel_date,
        scheduled_departure_time=departure_time,
        cabin_class=cabin_class,
    )


def test_cross_source_min_valid_fare_policy():
    """Verify standard prototype cross-source policy (MIN_VALID_FARE).

    Example from specification:
    DEL -> BOM, T+7
    SG 101: SpiceJet Direct ₹7,800, MakeMyTrip ₹8,100 -> SG 101: ₹7,800
    SG 102: SpiceJet Direct ₹8,000, Goibibo ₹7,900    -> SG 102: ₹7,900
    """
    quotes = [
        # SG 101 across 2 sources
        make_quote_for_reconciliation("SG 101", 7800.0, data_source_id=1, source_type="AIRLINE_DIRECT", observation_id=101),
        make_quote_for_reconciliation("SG 101", 8100.0, data_source_id=2, source_type="OTA", observation_id=102),
        # SG 102 across 2 sources
        make_quote_for_reconciliation("SG 102", 8000.0, data_source_id=1, source_type="AIRLINE_DIRECT", observation_id=201),
        make_quote_for_reconciliation("SG 102", 7900.0, data_source_id=3, source_type="OTA", observation_id=202),
    ]

    reconciled = reconcile_cross_source_quotes(quotes, policy=CrossSourcePolicy.MIN_VALID_FARE)

    assert len(reconciled) == 2
    rec_by_flight = {rf.flight_number: rf for rf in reconciled}

    # SG 101 resolves to ₹7,800 from SpiceJet Direct
    rf101 = rec_by_flight["SG 101"]
    assert rf101.reconciled_fare == Decimal("7800.00")
    assert rf101.selected_source_id == 1
    assert rf101.all_quotes_count == 2
    assert len(rf101.source_observations) == 2

    # SG 102 resolves to ₹7,900 from OTA
    rf102 = rec_by_flight["SG 102"]
    assert rf102.reconciled_fare == Decimal("7900.00")
    assert rf102.selected_source_id == 3
    assert rf102.all_quotes_count == 2


def test_cross_source_direct_preferred_policy():
    """Verify AIRLINE_DIRECT_PREFERRED policy chooses direct carrier even if slightly higher."""
    quotes = [
        make_quote_for_reconciliation("SG 102", 8000.0, data_source_id=1, source_type="AIRLINE_DIRECT"),
        make_quote_for_reconciliation("SG 102", 7900.0, data_source_id=3, source_type="OTA"),
    ]

    reconciled = reconcile_cross_source_quotes(quotes, policy=CrossSourcePolicy.AIRLINE_DIRECT_PREFERRED)

    assert len(reconciled) == 1
    assert reconciled[0].reconciled_fare == Decimal("8000.00")
    assert reconciled[0].selected_source_id == 1


def test_cross_source_mean_and_median_policies():
    """Verify MEAN and MEDIAN cross-source reconciliation policies."""
    quotes = [
        make_quote_for_reconciliation("SG 101", 7000.0, data_source_id=1),
        make_quote_for_reconciliation("SG 101", 7200.0, data_source_id=2),
        make_quote_for_reconciliation("SG 101", 7400.0, data_source_id=3),
    ]

    # Mean: (7000 + 7200 + 7400) / 3 = 7200.00
    rec_mean = reconcile_cross_source_quotes(quotes, policy=CrossSourcePolicy.MEAN_VALID_FARE)
    assert rec_mean[0].reconciled_fare == Decimal("7200.00")

    # Median: 7200.00
    rec_med = reconcile_cross_source_quotes(quotes, policy=CrossSourcePolicy.MEDIAN_VALID_FARE)
    assert rec_med[0].reconciled_fare == Decimal("7200.00")


def test_single_quote_unchanged():
    """Verify flights quoted by only a single source pass through with exact fare."""
    quotes = [
        make_quote_for_reconciliation("SG 205", 6500.0, data_source_id=1),
    ]
    reconciled = reconcile_cross_source_quotes(quotes)
    assert len(reconciled) == 1
    assert reconciled[0].flight_number == "SG 205"
    assert reconciled[0].reconciled_fare == Decimal("6500.00")
    assert reconciled[0].all_quotes_count == 1
