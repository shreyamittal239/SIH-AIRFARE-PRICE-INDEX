"""Unit tests for Yatra OTA flight data collector."""

from datetime import date, time
from decimal import Decimal
import pytest

from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.collectors.playwright.yatra_collector import (
    YatraCollector,
    parse_time_string,
    parse_fare_string,
    parse_flight_number,
    parse_yatra_flight_card,
)


# Real DOM text line samples captured directly from Yatra results page
YATRA_SAMPLE_AIX = [
    "Air India Express",
    "IX-1165/1027",
    "05:45",
    "New Delhi",
    "11:05",
    "Mumbai",
    "5h 20m",
    "1 Stop",
    "5,667",
    "View Fares",
]

YATRA_SAMPLE_SPICEJET = [
    "SpiceJet",
    "SG-9091",
    "05:30",
    "New Delhi",
    "07:50",
    "Mumbai",
    "2h 20m",
    "Non Stop",
    "6,447",
    "View Fares",
]

YATRA_SAMPLE_AKASA = [
    "Akasa Air",
    "QP-2074",
    "09:20",
    "New Delhi",
    "11:35",
    "Navi Mumbai",
    "2h 15m",
    "Non Stop",
    "6,500",
    "View Fares",
]

YATRA_SAMPLE_INDIGO = [
    "IndiGo",
    "6E-2721",
    "09:55",
    "New Delhi",
    "11:55",
    "Navi Mumbai",
    "2h 00m",
    "Non Stop",
    "6,529",
    "View Fares",
]

YATRA_SAMPLE_NEXT_DAY = [
    "Air India Express",
    "IX-1392/1027",
    "23:25",
    "New Delhi",
    "11:05",
    "+ 1 day",
    "Mumbai",
    "11h 40m",
    "1 Stop",
    "6,469",
    "View Fares",
]


def test_parse_time_string():
    """Verify parsing of valid and invalid time strings."""
    assert parse_time_string("05:45") == time(5, 45)
    assert parse_time_string("23:25") == time(23, 25)
    assert parse_time_string("00:05") == time(0, 5)
    assert parse_time_string("9:30") == time(9, 30)
    assert parse_time_string("invalid") is None
    assert parse_time_string("5h 20m") is None


def test_parse_fare_string():
    """Verify parsing and normalization of currency strings."""
    assert parse_fare_string("5,667") == Decimal("5667.00")
    assert parse_fare_string("Rs. 6,447") == Decimal("6447.00")
    assert parse_fare_string("₹ 6,529.00") == Decimal("6529.00")
    assert parse_fare_string("Free") is None
    assert parse_fare_string("") is None


def test_parse_flight_number():
    """Verify normalization of flight number formats."""
    assert parse_flight_number("SG-9091") == "SG 9091"
    assert parse_flight_number("6E-2721") == "6E 2721"
    assert parse_flight_number("IX-1165/1027") == "IX 1165/1027"
    assert parse_flight_number("QP-2074") == "QP 2074"
    assert parse_flight_number("AI 101") == "AI 101"


def test_parse_yatra_flight_card_spicejet():
    """Verify FlightQuote conversion from SpiceJet card on Yatra."""
    t_date = date(2026, 9, 16)
    quote = parse_yatra_flight_card(
        lines=YATRA_SAMPLE_SPICEJET,
        origin="DEL",
        destination="BOM",
        travel_date=t_date,
    )

    assert quote is not None
    assert isinstance(quote, FlightQuote)
    assert quote.airline == "SpiceJet"
    assert quote.flight_number == "SG 9091"
    assert quote.origin == "DEL"
    assert quote.destination == "BOM"
    assert quote.travel_date == t_date
    assert quote.departure_time == time(5, 30)
    assert quote.arrival_time == time(7, 50)
    assert quote.stops == 0
    assert quote.total_fare == Decimal("6447.00")
    assert quote.currency == "INR"
    assert quote.source == "Yatra"
    assert quote.availability is True
    assert quote.cabin_class == "ECONOMY"
    assert quote.fare_family is None
    assert quote.baggage is None


def test_parse_yatra_flight_card_air_india_express():
    """Verify FlightQuote conversion for Air India Express multi-leg flight."""
    t_date = date(2026, 9, 16)
    quote = parse_yatra_flight_card(
        lines=YATRA_SAMPLE_AIX,
        origin="DEL",
        destination="BOM",
        travel_date=t_date,
    )

    assert quote is not None
    assert quote.airline == "Air India Express"
    assert quote.flight_number == "IX 1165/1027"
    assert quote.stops == 1
    assert quote.total_fare == Decimal("5667.00")
    assert quote.departure_time == time(5, 45)
    assert quote.arrival_time == time(11, 5)


def test_parse_yatra_flight_card_akasa_and_indigo():
    """Verify FlightQuote conversion for Akasa Air and IndiGo."""
    t_date = date(2026, 9, 16)
    akasa_quote = parse_yatra_flight_card(
        lines=YATRA_SAMPLE_AKASA,
        origin="DEL",
        destination="BOM",
        travel_date=t_date,
    )
    assert akasa_quote is not None
    assert akasa_quote.airline == "Akasa Air"
    assert akasa_quote.flight_number == "QP 2074"
    assert akasa_quote.total_fare == Decimal("6500.00")

    indigo_quote = parse_yatra_flight_card(
        lines=YATRA_SAMPLE_INDIGO,
        origin="DEL",
        destination="BOM",
        travel_date=t_date,
    )
    assert indigo_quote is not None
    assert indigo_quote.airline == "IndiGo"
    assert indigo_quote.flight_number == "6E 2721"
    assert indigo_quote.total_fare == Decimal("6529.00")


def test_parse_yatra_flight_card_next_day_arrival():
    """Verify flight card with +1 day arrival badge."""
    t_date = date(2026, 9, 16)
    quote = parse_yatra_flight_card(
        lines=YATRA_SAMPLE_NEXT_DAY,
        origin="DEL",
        destination="BOM",
        travel_date=t_date,
    )
    assert quote is not None
    assert quote.departure_time == time(23, 25)
    assert quote.arrival_time == time(11, 5)
    assert quote.total_fare == Decimal("6469.00")


def test_parse_malformed_card_returns_none():
    """Verify malformed or advertisement cards return None cleanly."""
    t_date = date(2026, 9, 16)
    # Insufficient lines
    assert parse_yatra_flight_card(["Ad", "Buy Now"], "DEL", "BOM", t_date) is None
    # No price
    no_price = ["SpiceJet", "SG-101", "10:00", "DEL", "12:00", "BOM", "Non Stop"]
    assert parse_yatra_flight_card(no_price, "DEL", "BOM", t_date) is None
    # No valid flight number
    no_fn = ["Some Travel Offer", "Special Deal", "10:00", "DEL", "12:00", "BOM", "5,000"]
    assert parse_yatra_flight_card(no_fn, "DEL", "BOM", t_date) is None


def test_build_search_url():
    """Verify construction of canonical Yatra air search trigger URL."""
    url = YatraCollector.build_search_url(
        origin="DEL",
        destination="BOM",
        travel_date=date(2026, 9, 16),
        adults=1,
        cabin_class="ECONOMY",
    )
    assert "https://flight.yatra.com/air-search-ui/dom2/trigger?" in url
    assert "origin=DEL" in url
    assert "destination=BOM" in url
    assert "flight_depart_date=16/09/2026" in url
    assert "class=Economy" in url
    assert "ADT=1" in url


@pytest.mark.skip(reason="Live network test requires live Yatra access; run on demand.")
def test_live_yatra_collector_run():
    """Live integration test: execute real search on Yatra and extract quotes."""
    collector = YatraCollector()
    quotes = collector.collect(
        origin="DEL",
        destination="BOM",
        travel_date=date.today() + pytest.importorskip("datetime").timedelta(days=7),
    )
    assert len(quotes) > 0
    assert all(q.source == "Yatra" for q in quotes)
    assert all(q.total_fare > Decimal("0") for q in quotes)
