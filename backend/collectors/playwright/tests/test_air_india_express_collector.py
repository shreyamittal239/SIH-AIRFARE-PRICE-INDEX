"""Unit tests for Air India Express direct flight data collector."""

from datetime import date, time
from decimal import Decimal
import os
import pytest

from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.collectors.playwright.air_india_express_collector import (
    AirIndiaExpressCollector,
    parse_time_string,
    parse_fare_string,
    parse_flight_number,
    verify_travel_date_rendered,
    parse_air_india_express_flight_card,
)


# Sample text lines from captured Air India Express direct non-stop flight card
SAMPLE_NON_STOP_MULTI_TIER = [
    "Air India Express",
    "IX 1452",
    "05:45",
    "DEL",
    "Indira Gandhi Intl Airport",
    "2h 45m",
    "Non-Stop",
    "08:30",
    "BLR",
    "Kempegowda Intl Airport",
    "Xpress Lite",
    "₹ 5,149",
    "Cabin baggage 7kg",
    "Xpress Value",
    "₹ 5,667",
    "15kg check-in",
    "Xpress Flex",
    "₹ 6,299",
    "Free date changes",
    "Xpress Biz",
    "₹ 12,499",
    "Recliner seat & meal",
]

# Sample text lines from captured Air India Express multi-segment connecting flight card
SAMPLE_CONNECTING_MULTI_TIER = [
    "Air India Express",
    "IX 1165 / IX 1027",
    "05:45",
    "DEL",
    "5h 20m",
    "1 Stop",
    "Layover in BLR (1h 10m)",
    "11:05",
    "BOM",
    "Xpress Value",
    "₹ 5,667",
    "Xpress Flex",
    "₹ 6,469",
]

# Sample with promotional badges and discount banners that must NOT be parsed as total_fare
SAMPLE_WITH_PROMOTIONAL_BADGES = [
    "Air India Express",
    "IX 1802",
    "09:15",
    "DEL",
    "2h 20m",
    "Non-Stop",
    "11:35",
    "BOM",
    "Save ₹1000 off on Axis Bank Cards",
    "Date Change at ₹99",
    "Xpress Value",
    "₹ 6,447",
]

# Sample with collapsed / single fare display without explicit tier label
SAMPLE_COLLAPSED_SINGLE_FARE = [
    "Air India Express",
    "IX-2451",
    "14:20",
    "DEL",
    "Direct",
    "16:40",
    "BOM",
    "₹ 7,200",
    "Select",
]


# ==============================================================================
# 1. Parsing Helper Unit Tests
# ==============================================================================

def test_parse_flight_number_single_leg():
    """Verify single-leg non-stop flight number normalization."""
    assert parse_flight_number("IX 1452") == "IX 1452"
    assert parse_flight_number("IX-1452") == "IX 1452"
    assert parse_flight_number("IX1452") == "IX 1452"
    assert parse_flight_number("ix 902") == "IX 902"
    assert parse_flight_number("AI 101") is None
    assert parse_flight_number("SG 162") is None


def test_parse_flight_number_multi_segment():
    """Verify multi-segment flight numbers preserve both legs without loss."""
    assert parse_flight_number("IX 1165 / IX 1027") == "IX 1165/1027"
    assert parse_flight_number("IX-1165/1027") == "IX 1165/1027"
    assert parse_flight_number("IX 1165/IX 1027") == "IX 1165/1027"
    assert parse_flight_number("ix 1392 / ix 1027") == "IX 1392/1027"


def test_parse_fare_string_valid():
    """Verify valid rupee fare parsing into Decimal."""
    assert parse_fare_string("₹ 5,667") == Decimal("5667.00")
    assert parse_fare_string("₹5,149.00") == Decimal("5149.00")
    assert parse_fare_string("INR 12,499") == Decimal("12499.00")
    assert parse_fare_string("Rs. 6447") == Decimal("6447.00")
    assert parse_fare_string("7200") == Decimal("7200.00")


def test_parse_fare_string_rejects_promotions_and_invalid():
    """Verify rejection of promotional discounts, fees, and time strings."""
    assert parse_fare_string("₹1000 off") is None
    assert parse_fare_string("Save ₹1000") is None
    assert parse_fare_string("Date Change at ₹99") is None
    assert parse_fare_string("₹99") is None  # Below 500 threshold
    assert parse_fare_string("05:45") is None  # Time
    assert parse_fare_string("View Fares") is None
    assert parse_fare_string("") is None
    assert parse_fare_string(None) is None


def test_parse_time_string():
    """Verify departure and arrival time string parsing."""
    assert parse_time_string("05:45") == time(5, 45)
    assert parse_time_string("11:05+1") == time(11, 5)
    assert parse_time_string("23:59") == time(23, 59)
    assert parse_time_string("2h 45m") is None
    assert parse_time_string("DEL") is None


# ==============================================================================
# 2. Travel-Date Validation Tests
# ==============================================================================

def test_verify_travel_date_rendered_success():
    """Verify date check passes when rendered DOM contains target travel date."""
    target = date(2026, 9, 17)
    sample_text = (
        "DEL - BOM 17 Sep 2026 + Add Return Modify "
        "Mon, 14 Sep Tue, 15 Sep Wed, 16 Sep Thu, 17 Sep Fri, 18 Sep "
        "IX 1165 / IX 1027 05:45 DEL 11:05 BOM ₹ 5,667"
    )
    is_valid, msg = verify_travel_date_rendered(sample_text, target)
    assert is_valid is True
    assert "verified" in msg.lower()


def test_verify_travel_date_rendered_detects_no_inventory():
    """Verify date check returns False when zero-flights notice is present."""
    target = date(2026, 9, 17)
    no_flights_text = (
        "DEL - BOM 17 Sep 2026 "
        "Sorry, no flights found on this date! "
        "Try the closest available date to continue your search."
    )
    is_valid, msg = verify_travel_date_rendered(no_flights_text, target)
    assert is_valid is False
    assert "no inventory" in msg.lower()


def test_verify_travel_date_rendered_rejects_mismatched_adjacent_date():
    """Verify rejection when the page only renders adjacent dates without requested date."""
    target = date(2026, 9, 17)
    adjacent_text = (
        "DEL - BOM 15 Sep 2026 "
        "Mon, 14 Sep Tue, 15 Sep Wed, 16 Sep "
        "IX 1452 05:45 DEL 08:30 BLR ₹ 5,149"
    )
    is_valid, msg = verify_travel_date_rendered(adjacent_text, target)
    assert is_valid is False
    assert "does not contain target date" in msg


# ==============================================================================
# 3. Flight Card Parsing & Multi-Tier Extraction Tests
# ==============================================================================

def test_parse_non_stop_multi_tier_produces_distinct_quotes():
    """Verify that 4 distinct fare tiers on the same physical flight produce 4 FlightQuotes."""
    t_date = date(2026, 9, 17)
    quotes = parse_air_india_express_flight_card(
        lines=SAMPLE_NON_STOP_MULTI_TIER,
        origin="DEL",
        destination="BLR",
        travel_date=t_date,
    )

    assert len(quotes) == 4

    # All quotes share the exact same physical flight identity
    for q in quotes:
        assert q.airline == "Air India Express"
        assert q.flight_number == "IX 1452"
        assert q.origin == "DEL"
        assert q.destination == "BLR"
        assert q.travel_date == t_date
        assert q.departure_time == time(5, 45)
        assert q.arrival_time == time(8, 30)
        assert q.stops == 0
        assert q.source == "Air India Express Direct"
        assert q.availability is True
        assert q.currency == "INR"

    # Verify each tier preserves its distinct fare family, fare amount, and cabin
    tiers_found = {q.fare_family: q for q in quotes}
    assert "Xpress Lite" in tiers_found
    assert "Xpress Value" in tiers_found
    assert "Xpress Flex" in tiers_found
    assert "Xpress Biz" in tiers_found

    assert tiers_found["Xpress Lite"].total_fare == Decimal("5149.00")
    assert tiers_found["Xpress Lite"].cabin_class == "ECONOMY"

    assert tiers_found["Xpress Value"].total_fare == Decimal("5667.00")
    assert tiers_found["Xpress Value"].cabin_class == "ECONOMY"

    assert tiers_found["Xpress Flex"].total_fare == Decimal("6299.00")
    assert tiers_found["Xpress Flex"].cabin_class == "ECONOMY"

    assert tiers_found["Xpress Biz"].total_fare == Decimal("12499.00")
    assert tiers_found["Xpress Biz"].cabin_class == "BUSINESS"


def test_parse_multi_segment_itinerary_preserves_both_legs():
    """Verify multi-segment connecting flight preserves both legs and stops count."""
    t_date = date(2026, 9, 17)
    quotes = parse_air_india_express_flight_card(
        lines=SAMPLE_CONNECTING_MULTI_TIER,
        origin="DEL",
        destination="BOM",
        travel_date=t_date,
    )

    assert len(quotes) == 2  # Xpress Value and Xpress Flex

    for q in quotes:
        # Crucial check: does not collapse IX 1165 / IX 1027 to just IX 1165
        assert q.flight_number == "IX 1165/1027"
        assert q.stops == 1
        assert q.origin == "DEL"
        assert q.destination == "BOM"
        assert q.departure_time == time(5, 45)
        assert q.arrival_time == time(11, 5)

    fares = {q.fare_family: q.total_fare for q in quotes}
    assert fares["Xpress Value"] == Decimal("5667.00")
    assert fares["Xpress Flex"] == Decimal("6469.00")


def test_parse_filters_promotional_discount_badges():
    """Verify promotional bank discounts (₹1000 off) are not mistakenly parsed as total_fare."""
    t_date = date(2026, 9, 17)
    quotes = parse_air_india_express_flight_card(
        lines=SAMPLE_WITH_PROMOTIONAL_BADGES,
        origin="DEL",
        destination="BOM",
        travel_date=t_date,
    )

    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.flight_number == "IX 1802"
    # Ensure total_fare is the actual fare ₹6,447, not ₹1000 or ₹99
    assert quote.total_fare == Decimal("6447.00")
    assert quote.fare_family == "Xpress Value"


def test_parse_collapsed_single_fare():
    """Verify single fare on collapsed card is extracted with standard Xpress Value fallback."""
    t_date = date(2026, 9, 17)
    quotes = parse_air_india_express_flight_card(
        lines=SAMPLE_COLLAPSED_SINGLE_FARE,
        origin="DEL",
        destination="BOM",
        travel_date=t_date,
    )

    assert len(quotes) == 1
    q = quotes[0]
    assert q.flight_number == "IX 2451"
    assert q.total_fare == Decimal("7200.00")
    assert q.stops == 0
    assert q.departure_time == time(14, 20)
    assert q.arrival_time == time(16, 40)


def test_parse_malformed_card_returns_empty():
    """Verify invalid or ad blocks return empty list without raising exceptions."""
    t_date = date(2026, 9, 17)
    assert parse_air_india_express_flight_card([], "DEL", "BOM", t_date) == []
    assert parse_air_india_express_flight_card(["Hotels and Cabs", "Manage Booking"], "DEL", "BOM", t_date) == []
    # No price
    assert parse_air_india_express_flight_card(["Air India Express", "IX 101", "10:00", "12:00"], "DEL", "BOM", t_date) == []
    # No flight number
    assert parse_air_india_express_flight_card(["Special Deal", "₹ 5,000", "Book Now", "DEL", "BOM"], "DEL", "BOM", t_date) == []


# ==============================================================================
# 4. Collector URL Construction & Configuration Tests
# ==============================================================================

def test_build_canonical_search_url():
    """Verify construction of canonical Air India Express flight availability URL."""
    url = AirIndiaExpressCollector.build_search_url(
        origin="DEL",
        destination="BOM",
        travel_date=date(2026, 9, 17),
        adults=1,
        children=0,
        infants=0,
        non_stop_only=False,
    )
    assert "https://www.airindiaexpress.com/flight-availability?" in url
    assert "/DEL/BOM/2026-09-17/N/1/0/0/0/0/0/0/O/N/INR/ST/0" in url

    # Test non-stop flag
    url_ns = AirIndiaExpressCollector.build_search_url(
        origin="DEL",
        destination="BLR",
        travel_date=date(2026, 9, 20),
        adults=2,
        non_stop_only=True,
    )
    assert "/DEL/BLR/2026-09-20/Y/2/0/0/0/0/0/0/O/N/INR/ST/0" in url_ns



def test_multi_fare_families_fingerprint_distinct_and_not_deduplicated():
    """Verify that multiple fare tiers for the same physical flight produce distinct fingerprints.
    
    Ensures ingestion does not deduplicate different fare families (e.g. Xpress Lite vs Value).
    """
    from backend.processing.ingestion.fingerprint import compute_flight_fingerprint

    t_date = date(2026, 9, 17)
    quotes = parse_air_india_express_flight_card(
        lines=SAMPLE_NON_STOP_MULTI_TIER,
        origin="DEL",
        destination="BLR",
        travel_date=t_date,
    )
    assert len(quotes) == 4

    fps = [compute_flight_fingerprint(q) for q in quotes]
    # All 4 fingerprints must be completely unique
    assert len(set(fps)) == 4

    # Simulated intra-run deduplication check:
    batch_fingerprints = set()
    accepted_quotes = []
    for q in quotes:
        fp = compute_flight_fingerprint(q)
        if fp not in batch_fingerprints:
            batch_fingerprints.add(fp)
            accepted_quotes.append(q)

    # Ingestion retains all 4 distinct fare products
    assert len(accepted_quotes) == 4


# ==============================================================================
# 5. Live E2E Test (Opt-in via RUN_LIVE_AIRLINE_TEST)
# ==============================================================================

@pytest.mark.skipif(
    os.getenv("RUN_LIVE_AIRLINE_TEST", "false").lower() != "true",
    reason="Live Air India Express portal test skipped by default. Set RUN_LIVE_AIRLINE_TEST=true to run.",
)
def test_live_air_india_express_collector_run():
    """Live portal integration test: execute real availability search and extract quotes."""
    collector = AirIndiaExpressCollector()
    try:
        t_date = date(2026, 9, 17)
        quotes = collector.collect(
            origin="DEL",
            destination="BOM",
            travel_date=t_date,
        )
        # Note: If no flights exist on this specific date, quotes is gracefully []
        assert isinstance(quotes, list)
        for q in quotes:
            assert q.airline == "Air India Express"
            assert q.origin == "DEL"
            assert q.destination == "BOM"
            assert q.travel_date == t_date
            assert q.total_fare > Decimal("0")
            assert q.source == "Air India Express Direct"
    finally:
        collector.close()
