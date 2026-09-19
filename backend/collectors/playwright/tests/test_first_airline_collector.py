"""Unit and integration tests for the first airline collector (SpiceJet)."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import os
from unittest.mock import MagicMock, patch
import pytest
from pydantic import ValidationError
from playwright.sync_api import Error as PlaywrightError

from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.collectors.playwright.collectors.first_airline_collector import (
    FirstAirlineCollector,
    parse_time_string,
    parse_fare_string,
    parse_flight_card_lines,
)
from backend.collectors.orchestrator import (
    SpiceJetCollectorAdapter,
    CollectionStatus,
    is_transient_technical_error,
)


def test_flight_quote_valid_schema():
    """Verify FlightQuote model creation with required and optional fields."""
    quote = FlightQuote(
        airline="SpiceJet",
        flight_number="SG 162",
        origin="DEL",
        destination="BOM",
        travel_date=date(2026, 9, 15),
        departure_time=time(19, 55),
        arrival_time=time(22, 40),
        total_fare=Decimal("7469.00"),
        source="SpiceJet Direct",
    )

    assert quote.airline == "SpiceJet"
    assert quote.flight_number == "SG 162"
    assert quote.origin == "DEL"
    assert quote.destination == "BOM"
    assert quote.cabin_class == "ECONOMY"
    assert quote.currency == "INR"
    assert quote.stops == 0
    assert quote.total_fare == Decimal("7469.00")
    assert quote.baggage is None
    assert quote.fare_family is None
    assert isinstance(quote.observed_at, datetime)


def test_flight_quote_nullable_fields():
    """Verify that optional fields allow None values without validation errors."""
    quote = FlightQuote(
        airline="SpiceJet",
        flight_number="SG 2802",
        origin="DEL",
        destination="BOM",
        travel_date=date(2026, 9, 15),
        departure_time=None,
        arrival_time=None,
        fare_family=None,
        stops=None,
        total_fare=Decimal("15200.50"),
        baggage=None,
    )
    assert quote.departure_time is None
    assert quote.arrival_time is None
    assert quote.stops is None
    assert quote.baggage is None


def test_flight_quote_validation_failure():
    """Verify that missing required fields raise ValidationError."""
    with pytest.raises(ValidationError):
        # Missing total_fare and travel_date
        FlightQuote(
            airline="SpiceJet",
            flight_number="SG 101",
            origin="DEL",
            destination="BOM",
        )


def test_parse_time_string():
    """Test standard and rollover time string parsing."""
    assert parse_time_string("19:55") == time(19, 55)
    assert parse_time_string("01:25+1") == time(1, 25)
    assert parse_time_string("00:05") == time(0, 5)
    assert parse_time_string("invalid") is None
    assert parse_time_string("") is None


def test_parse_fare_string():
    """Test currency symbol and formatted price string parsing."""
    assert parse_fare_string("₹ 7,469") == Decimal("7469")
    assert parse_fare_string("₹20,769.50") == Decimal("20769.50")
    assert parse_fare_string("7085") == Decimal("7085")
    assert parse_fare_string("FREE") is None


def test_parse_flight_card_lines_sample_1():
    """Test parsing real captured SpiceJet flight card 1."""
    card_lines = [
        "19:55",
        "DEL",
        "Flight Details",
        "2h 45m",
        "22:40",
        "BOM",
        "SG 162",
        "Direct",
        "₹ 7,469",
        "Earn 232 Points",
        "₹ 7,889",
        "Earn 248 Points",
        "₹ 8,781",
        "Earn 284 Points",
    ]
    t_date = date(2026, 9, 15)
    quote = parse_flight_card_lines(card_lines, "DEL", "BOM", t_date)

    assert quote is not None
    assert quote.airline == "SpiceJet"
    assert quote.flight_number == "SG 162"
    assert quote.origin == "DEL"
    assert quote.destination == "BOM"
    assert quote.travel_date == t_date
    assert quote.departure_time == time(19, 55)
    assert quote.arrival_time == time(22, 40)
    assert quote.stops == 0
    assert quote.total_fare == Decimal("7469")
    assert quote.fare_family == "SpiceSaver"
    assert quote.currency == "INR"


def test_parse_flight_card_lines_sample_2():
    """Test parsing real captured SpiceJet flight card 2 with next-day arrival."""
    card_lines = [
        "22:45",
        "DEL",
        "Flight Details",
        "2h 40m",
        "01:25+1",
        "BOM",
        "SG 2802",
        "Direct",
        "₹ 20,769",
        "Earn 740 Points",
        "₹ 21,189",
        "Earn 756 Points",
        "₹ 23,613",
        "Earn 848 Points",
    ]
    t_date = date(2026, 9, 15)
    quote = parse_flight_card_lines(card_lines, "DEL", "BOM", t_date)

    assert quote is not None
    assert quote.flight_number == "SG 2802"
    assert quote.departure_time == time(22, 45)
    assert quote.arrival_time == time(1, 25)
    assert quote.stops == 0
    assert quote.total_fare == Decimal("20769")


def test_parse_flight_card_lines_invalid():
    """Test that missing required indicators returns None without throwing."""
    # No flight number or fare
    invalid_lines = ["Some banner text", "Hotels and Cabs", "Manage Booking"]
    assert parse_flight_card_lines(invalid_lines, "DEL", "BOM", date.today()) is None


def test_collector_configuration_defaults():
    """Test default values and configurable overrides on FirstAirlineCollector."""
    collector = FirstAirlineCollector()
    assert collector.origin == "DEL"
    assert collector.destination == "BOM"
    assert collector.passengers == 1
    assert collector.cabin_class == "ECONOMY"
    # Expected advance date default is T+7
    expected_default_date = date.today() + timedelta(days=7)
    assert collector.travel_date == expected_default_date
    assert "from=DEL" in collector.build_search_url()
    assert "to=BOM" in collector.build_search_url()

    # Custom override
    custom = FirstAirlineCollector(
        origin="BOM",
        destination="GOI",
        travel_date=date(2026, 10, 1),
        passengers=2,
    )
    assert custom.origin == "BOM"
    assert custom.destination == "GOI"
    assert custom.travel_date == date(2026, 10, 1)
    assert custom.passengers == 2


# ==============================================================================
# Focused Validation Tests for Zero-Inventory & Error Distinction
# ==============================================================================

def test_1_valid_spicejet_inventory():
    """TEST 1 — Valid SpiceJet inventory.

    Given a page containing valid SpiceJet flight cards:
    - Flight cards are extracted
    - FlightQuote objects are created
    - Existing fare/time/route parsing remains unchanged
    """
    mock_page = MagicMock()
    mock_page.url = "https://www.spicejet.com/search?from=DEL&to=BOM"
    mock_body = MagicMock()
    mock_body.inner_text.return_value = "Search Results SG 162 19:55 DEL BOM 22:40 ₹ 7,469"
    mock_page.locator.return_value = mock_body
    mock_page.wait_for_function.return_value = None

    collector = FirstAirlineCollector(
        origin="DEL",
        destination="BOM",
        travel_date=date(2026, 9, 23),
    )
    collector.page = mock_page

    raw_card = {
        "flight_number": "SG 162",
        "text": "19:55\nDEL\n2h 45m\n22:40\nBOM\nSG 162\nDirect\n₹ 7,469\nEarn 232 Points",
    }

    with patch.object(collector, "search_via_form", return_value=True), \
         patch.object(collector, "extract_fares", return_value=[raw_card]):
        quotes = collector.collect_quotes()

    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.airline == "SpiceJet"
    assert quote.flight_number == "SG 162"
    assert quote.origin == "DEL"
    assert quote.destination == "BOM"
    assert quote.travel_date == date(2026, 9, 23)
    assert quote.departure_time == time(19, 55)
    assert quote.arrival_time == time(22, 40)
    assert quote.total_fare == Decimal("7469")
    assert quote.currency == "INR"


def test_2_explicit_zero_inventory():
    """TEST 2 — Explicit zero inventory.

    Given a page containing SpiceJet's actual 'Unfortunately, there are no flights available.' DOM state:
    - Collector returns successful zero-inventory result
    - No 25-second flight-card timeout occurs
    - Zero FlightQuote records are produced
    - No scrape error is raised
    """
    mock_page = MagicMock()
    mock_page.url = "https://www.spicejet.com/search?from=BLR&to=DEL"
    mock_body = MagicMock()
    mock_body.inner_text.return_value = (
        "BLR to DEL, 17 Sep 2026\n"
        "•\n"
        "1 Adult\n"
        "Modify Search\n"
        "Unfortunately, there are no flights available.\n"
        "Please search again with a different date.\n"
        "Search again"
    )
    mock_page.locator.return_value = mock_body
    mock_page.wait_for_function.return_value = None

    collector = FirstAirlineCollector(
        origin="BLR",
        destination="DEL",
        travel_date=date(2026, 9, 17),
    )
    collector.page = mock_page

    with patch.object(collector, "search_via_form", return_value=True):
        quotes = collector.collect_quotes()

    assert collector.is_zero_inventory is True
    assert "unfortunately, there are no flights available" in (collector.no_inventory_reason or "").lower()
    assert quotes == []

    # Verify adapter behavior
    adapter = SpiceJetCollectorAdapter()
    with patch("backend.collectors.playwright.collectors.first_airline_collector.FirstAirlineCollector", return_value=collector):
        res = adapter.collect(origin="BLR", destination="DEL", travel_date=date(2026, 9, 17))

    assert res.status == CollectionStatus.SUCCESS_NO_INVENTORY
    assert res.quotes == []
    assert res.error_message is None
    assert "no flights available" in (res.no_inventory_reason or "").lower()


def test_3_missing_flight_cards_without_explicit_zero_inventory():
    """TEST 3 — Missing flight cards without explicit zero-inventory.

    Given a page where:
    - No valid flight cards exist
    - No explicit zero-inventory message exists
    Expected:
    - Collector eventually reports a genuine DOM/scrape failure
    - It must NOT silently classify the task as zero inventory
    """
    mock_page = MagicMock()
    mock_page.url = "https://www.spicejet.com/search?from=DEL&to=BOM"
    mock_page.wait_for_function.side_effect = PlaywrightError("Timeout 25000ms exceeded while waiting for function")

    collector = FirstAirlineCollector(
        origin="DEL",
        destination="BOM",
        travel_date=date(2026, 9, 23),
    )
    collector.page = mock_page

    with patch.object(collector, "search_via_form", return_value=True):
        with pytest.raises(PlaywrightError) as exc_info:
            collector.collect_quotes()

    assert "Timeout 25000ms exceeded" in str(exc_info.value)
    assert collector.is_zero_inventory is False

    # Verify adapter classifies as FAILED, NOT SUCCESS_NO_INVENTORY
    adapter = SpiceJetCollectorAdapter()
    with patch("backend.collectors.playwright.collectors.first_airline_collector.FirstAirlineCollector", return_value=collector):
        res = adapter.collect(origin="DEL", destination="BOM", travel_date=date(2026, 9, 23))

    assert res.status == CollectionStatus.FAILED
    assert res.quotes == []
    assert "Timeout" in (res.error_message or "")


def test_4_dns_failure():
    """TEST 4 — DNS failure.

    Given navigation raises net::ERR_NAME_NOT_RESOLVED:
    Expected:
    - Classify as network/navigation failure
    - Retry according to existing policy (is_transient_technical_error == True)
    - Never classify as zero inventory
    """
    mock_page = MagicMock()
    dns_error = PlaywrightError("net::ERR_NAME_NOT_RESOLVED at https://www.spicejet.com/")
    mock_page.goto.side_effect = dns_error

    collector = FirstAirlineCollector(
        origin="DEL",
        destination="IXL",
        travel_date=date(2026, 10, 1),
    )
    collector.page = mock_page

    adapter = SpiceJetCollectorAdapter()
    with patch("backend.collectors.playwright.collectors.first_airline_collector.FirstAirlineCollector", return_value=collector):
        res = adapter.collect(origin="DEL", destination="IXL", travel_date=date(2026, 10, 1))

    assert res.status == CollectionStatus.FAILED
    assert res.quotes == []
    assert "ERR_NAME_NOT_RESOLVED" in (res.error_message or "")
    # Ensure it is NOT treated as zero inventory
    assert res.status != CollectionStatus.SUCCESS_NO_INVENTORY
    # Ensure orchestrator identifies it as retryable technical error
    assert is_transient_technical_error(res.error_message or "") is True


def test_5_existing_successful_spicejet_route():
    """TEST 5 — Existing successful SpiceJet route.

    Use an existing known-good case such as DEL-BOM and verify that
    the fix does not break normal extraction.
    """
    t_date = date(2026, 9, 23)
    collector = FirstAirlineCollector(
        origin="DEL",
        destination="BOM",
        travel_date=t_date,
    )
    assert collector.origin == "DEL"
    assert collector.destination == "BOM"
    assert collector.travel_date == t_date
    assert "from=DEL" in collector.build_search_url()
    assert "to=BOM" in collector.build_search_url()
    assert "departure=2026-09-23" in collector.build_search_url()

    # Verify flight card parsing on DEL-BOM
    card_lines = [
        "08:10",
        "DEL",
        "2h 15m",
        "10:25",
        "BOM",
        "SG 8168",
        "Non-Stop",
        "₹ 6,299",
        "Earn 200 Points",
    ]
    quote = parse_flight_card_lines(card_lines, "DEL", "BOM", t_date)
    assert quote is not None
    assert quote.airline == "SpiceJet"
    assert quote.flight_number == "SG 8168"
    assert quote.origin == "DEL"
    assert quote.destination == "BOM"
    assert quote.travel_date == t_date
    assert quote.departure_time == time(8, 10)
    assert quote.arrival_time == time(10, 25)
    assert quote.stops == 0
    assert quote.total_fare == Decimal("6299")


def test_6_connecting_flights_multi_leg_parsing():
    """TEST 6 — Connecting multi-leg flights with comma-separated flight numbers and halts.

    Reproduces the exact live page state discovered on DEL-HYD T+45, CCU-BOM T+7, and CCU-BOM T+15:
    - Comma-separated flight numbers in DOM (e.g. 'SG 617, SG 688', 'SG 906, SG 162')
    - Halt notation (e.g. 'Connecting,with halt at VNS', 'Connecting,with halt at DEL')
    - Next-day arrival notation (e.g. '22:40+1')
    """
    # 1. DEL-HYD T+45 connecting flight
    del_hyd_lines = [
        "15:50",
        "DEL",
        "Flight Details",
        "6h 30m",
        "22:20",
        "HYD",
        "SG 617, SG 688",
        "Connecting,with halt at VNS",
        "₹ 12,243",
        "Earn 400 Points",
        "N/A",
        "Not Available",
        "₹ 15,183",
        "Earn 512 Points",
    ]
    t_date1 = date(2026, 10, 31)
    q1 = parse_flight_card_lines(del_hyd_lines, "DEL", "HYD", t_date1)
    assert q1 is not None
    assert q1.airline == "SpiceJet"
    assert q1.flight_number == "SG 617"
    assert q1.origin == "DEL"
    assert q1.destination == "HYD"
    assert q1.travel_date == t_date1
    assert q1.departure_time == time(15, 50)
    assert q1.arrival_time == time(22, 20)
    assert q1.stops == 1
    assert q1.total_fare == Decimal("12243")

    # 2. CCU-BOM T+7 connecting flight with next-day arrival
    ccu_bom_lines = [
        "23:20",
        "CCU",
        "Flight Details",
        "23h 20m",
        "22:40+1",
        "BOM",
        "SG 906, SG 162",
        "Connecting,with halt at DEL",
        "₹ 18,246",
        "Earn 600 Points",
        "N/A",
        "Not Available",
        "₹ 20,872",
        "Earn 700 Points",
    ]
    t_date2 = date(2026, 9, 23)
    q2 = parse_flight_card_lines(ccu_bom_lines, "CCU", "BOM", t_date2)
    assert q2 is not None
    assert q2.airline == "SpiceJet"
    assert q2.flight_number == "SG 906"
    assert q2.origin == "CCU"
    assert q2.destination == "BOM"
    assert q2.travel_date == t_date2
    assert q2.departure_time == time(23, 20)
    assert q2.arrival_time == time(22, 40)
    assert q2.stops == 1
    assert q2.total_fare == Decimal("18246")


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_AIRLINE_TEST", "false").lower() != "true",
    reason="Live airline portal test skipped by default. Set RUN_LIVE_AIRLINE_TEST=true to run.",
)
def test_live_collector_run():

    """Live portal integration test (opt-in via env var)."""
    collector = FirstAirlineCollector()
    try:
        quotes = collector.collect_quotes()
        assert len(quotes) > 0
        assert all(q.origin == "DEL" for q in quotes)
        assert all(q.destination == "BOM" for q in quotes)
    finally:
        collector.close()
