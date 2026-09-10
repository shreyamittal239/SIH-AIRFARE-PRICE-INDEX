"""Unit and integration tests for the first airline collector (SpiceJet)."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import os
import pytest
from pydantic import ValidationError

from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.collectors.playwright.collectors.first_airline_collector import (
    FirstAirlineCollector,
    parse_time_string,
    parse_fare_string,
    parse_flight_card_lines,
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
