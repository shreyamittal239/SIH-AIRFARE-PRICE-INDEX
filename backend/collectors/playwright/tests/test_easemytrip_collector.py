"""Unit tests for EaseMyTrip OTA flight data collector."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
import pytest

from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.collectors.playwright.easemytrip_collector import (
    EaseMyTripCollector,
    CollectorDateMismatchError,
    CollectorRouteMismatchError,
    parse_time_string,
    parse_fare_string,
    parse_flight_number,
    normalize_airline_name,
    parse_stops_string,
    verify_travel_date_rendered,
    verify_route_rendered,
    is_airport_compatible,
    parse_easemytrip_flight_card,
)
from backend.processing.ingestion.fingerprint import compute_flight_fingerprint


# Mock card dictionaries modeled directly from EaseMyTrip DOM evaluations
SAMPLE_AKASA_CARD = {
    "airline": "AkasaAir",
    "flight_number": "QP-1110",
    "departure_time": "10:30",
    "arrival_time": "12:45",
    "departure_airport": "New Delhi",
    "arrival_airport": "Mumbai",
    "duration": "02h 15m",
    "stops": "Non-stop",
    "price": "6530",
    "fare_family": "SAVER",
    "raw_text": "AkasaAir | QP-1110 | 10:30 | New Delhi | 02h 15m | Non-stop | 12:45 | Mumbai | 6,530 | Sat-19Sep2026",
}

SAMPLE_INDIGO_CARD = {
    "airline": "IndiGo",
    "flight_number": "6E-5096",
    "departure_time": "17:00",
    "arrival_time": "19:05",
    "departure_airport": "New Delhi",
    "arrival_airport": "Mumbai",
    "duration": "02h 05m",
    "stops": "Non-stop",
    "price": "6442.00",
    "fare_family": "SAVER",
    "raw_text": "IndiGo | 6E-5096 | 17:00 | New Delhi | 02h 05m | Non-stop | 19:05 | Mumbai | 6,442 | Sat-19Sep2026",
}

SAMPLE_CONNECTING_CARD = {
    "airline": "Air India Express",
    "flight_number": "IX-1165/1027",
    "departure_time": "05:45",
    "arrival_time": "11:05",
    "departure_airport": "New Delhi",
    "arrival_airport": "Mumbai",
    "duration": "05h 20m",
    "stops": "1-stop",
    "price": "6529",
    "fare_family": "SAVER",
    "raw_text": "Air India Express | IX-1165/1027 | 05:45 | New Delhi | 05h 20m | 1-stop | 11:05 | Mumbai | 6,529 | Sat-19Sep2026",
}

SAMPLE_SECONDARY_AIRPORT_CARD = {
    "airline": "IndiGo",
    "flight_number": "6E-5096",
    "departure_time": "17:00",
    "arrival_time": "19:05",
    "departure_airport": "Ghaziabad (28 km from New Delhi)",
    "arrival_airport": "Navi Mumbai (24 km from Mumbai)",
    "duration": "02h 05m",
    "stops": "Non-stop",
    "price": "6442",
    "fare_family": "SAVER",
    "raw_text": "IndiGo | 6E-5096 | 17:00 | Ghaziabad | 19:05 | Navi Mumbai | 6,442",
}


# ==============================================================================
# 1. URL Construction Tests
# ==============================================================================

def test_url_construction_default():
    """Verify default search URL generation with city names and formatting."""
    t_date = date(2026, 9, 19)
    url = EaseMyTripCollector.build_search_url("DEL", "BOM", t_date)
    assert "srch=DEL-Delhi-India|BOM-Mumbai-India|19/09/2026" in url
    assert "px=1-0-0" in url
    assert "cbn=0" in url
    assert "https://flight.easemytrip.com/FlightList/Index?" in url


def test_date_formatting_in_url():
    """Verify exact DD/MM/YYYY date formatting in URL."""
    t_date = date(2026, 12, 5)
    url = EaseMyTripCollector.build_search_url("DEL", "BLR", t_date)
    assert "05/12/2026" in url


def test_passenger_parameters_in_url():
    """Verify adult, child, infant parameter serialization."""
    t_date = date(2026, 9, 19)
    url = EaseMyTripCollector.build_search_url("DEL", "BOM", t_date, adults=2, children=1, infants=1)
    assert "px=2-1-1" in url


def test_cabin_mapping_in_url():
    """Verify cabin class code mapping (Economy=0, Premium=1, Business=2, First=3)."""
    t_date = date(2026, 9, 19)
    assert "cbn=0" in EaseMyTripCollector.build_search_url("DEL", "BOM", t_date, cabin_class="ECONOMY")
    assert "cbn=1" in EaseMyTripCollector.build_search_url("DEL", "BOM", t_date, cabin_class="PREMIUM_ECONOMY")
    assert "cbn=2" in EaseMyTripCollector.build_search_url("DEL", "BOM", t_date, cabin_class="BUSINESS")
    assert "cbn=3" in EaseMyTripCollector.build_search_url("DEL", "BOM", t_date, cabin_class="FIRST")


# ==============================================================================
# 2. Parsing & Normalization Unit Tests
# ==============================================================================

def test_airline_parsing():
    """Verify normalization of airline names."""
    assert normalize_airline_name("AkasaAir") == "Akasa Air"
    assert normalize_airline_name("akasa air") == "Akasa Air"
    assert normalize_airline_name("IndiGo") == "IndiGo"
    assert normalize_airline_name("AIR INDIA") == "Air India"
    assert normalize_airline_name("Air India Express") == "Air India Express"
    assert normalize_airline_name("SpiceJet") == "SpiceJet"
    assert normalize_airline_name("UnknownAirline") == "UnknownAirline"


def test_flight_number_normalization():
    """Verify flight number cleanup and hyphen stripping."""
    assert parse_flight_number("QP-1110") == "QP 1110"
    assert parse_flight_number("6E-5096") == "6E 5096"
    assert parse_flight_number("IX-1235") == "IX 1235"
    assert parse_flight_number("AI-887") == "AI 887"
    assert parse_flight_number("SG-9091") == "SG 9091"
    assert parse_flight_number("IX-1165/1027") == "IX 1165/1027"
    assert parse_flight_number("invalid") is None


def test_departure_arrival_parsing():
    """Verify parsing of time strings into datetime.time objects."""
    assert parse_time_string("10:30") == time(10, 30)
    assert parse_time_string("05:45") == time(5, 45)
    assert parse_time_string("23:59") == time(23, 59)
    assert parse_time_string("00:05") == time(0, 5)
    assert parse_time_string("invalid") is None
    assert parse_time_string("") is None


def test_stops_parsing():
    """Verify parsing of stop count strings."""
    assert parse_stops_string("Non-stop") == 0
    assert parse_stops_string("non stop") == 0
    assert parse_stops_string("direct") == 0
    assert parse_stops_string("1-stop") == 1
    assert parse_stops_string("1 stop") == 1
    assert parse_stops_string("2-stop") == 2
    assert parse_stops_string("") == 0


def test_fare_parsing():
    """Verify parsing and normalization of fare amounts."""
    assert parse_fare_string("6,530") == Decimal("6530.00")
    assert parse_fare_string("₹ 6,530") == Decimal("6530.00")
    assert parse_fare_string("Rs. 6442.00") == Decimal("6442.00")
    assert parse_fare_string("Lock Price ₹ 327") is None
    assert parse_fare_string("Get up to ₹7500 OFF") is None
    assert parse_fare_string("450") is None  # Below MIN_VALID_FARE threshold
    assert parse_fare_string("Free") is None


def test_currency():
    """Verify that extracted quotes always default to INR."""
    t_date = date(2026, 9, 19)
    quote = parse_easemytrip_flight_card(SAMPLE_AKASA_CARD, "DEL", "BOM", t_date)
    assert quote is not None
    assert quote.currency == "INR"


# ==============================================================================
# 3. Date & Route Safety Tests
# ==============================================================================

def test_date_validation_accepts_valid():
    """Verify date verification passes when target date is rendered."""
    t_date = date(2026, 9, 19)
    valid_text = "New Delhi → Mumbai | Sat 19 Sept 2026 [1 Adult] | FILTER"
    is_valid, msg = verify_travel_date_rendered(valid_text, t_date)
    assert is_valid is True
    assert "verified" in msg.lower()


def test_date_validation_rejects_mismatch():
    """Verify date verification fails and rejects mismatched adjacent dates."""
    t_date = date(2026, 9, 19)
    wrong_text = "New Delhi → Mumbai | Sun 20 Sept 2026 [1 Adult] | FILTER"
    is_valid, msg = verify_travel_date_rendered(wrong_text, t_date)
    assert is_valid is False
    assert "does not contain target date" in msg.lower()


def test_date_validation_rejects_no_inventory():
    """Verify date verification rejects pages showing explicit no-flights notification."""
    t_date = date(2026, 9, 19)
    no_flights_text = "Sorry, no flights found on this date. Please try another date."
    is_valid, msg = verify_travel_date_rendered(no_flights_text, t_date)
    assert is_valid is False
    assert "no inventory" in msg.lower()


def test_origin_destination_validation():
    """Verify route validation checks origin and destination in DOM text."""
    valid_text = "New Delhi to Mumbai Flights | Sat 19 Sept 2026"
    assert verify_route_rendered(valid_text, "DEL", "BOM")[0] is True

    mismatch_text = "Bangalore to Chennai Flights | Sat 19 Sept 2026"
    assert verify_route_rendered(mismatch_text, "DEL", "BOM")[0] is False


def test_airport_compatibility_filters_secondary_airports():
    """Verify strict airport compatibility checks exclude regional airports (HDO, NMI)."""
    # DEL should allow New Delhi / Delhi, but reject Ghaziabad / Hindon / Jewar
    assert is_airport_compatible("New Delhi", "DEL") is True
    assert is_airport_compatible("Indira Gandhi International", "DEL") is True
    assert is_airport_compatible("Ghaziabad (28 km from New Delhi)", "DEL") is False
    assert is_airport_compatible("Hindon Airport", "DEL") is False

    # BOM should allow Mumbai, but reject Navi Mumbai
    assert is_airport_compatible("Mumbai", "BOM") is True
    assert is_airport_compatible("CSMIA", "BOM") is True
    assert is_airport_compatible("Navi Mumbai (24 km from Mumbai)", "BOM") is False


def test_secondary_airport_card_is_skipped():
    """Verify that a flight from Ghaziabad/Hindon to Navi Mumbai is excluded when DEL-BOM is requested."""
    t_date = date(2026, 9, 19)
    quote = parse_easemytrip_flight_card(SAMPLE_SECONDARY_AIRPORT_CARD, "DEL", "BOM", t_date)
    assert quote is None


# ==============================================================================
# 4. Flight Card Parsing & Edge Cases
# ==============================================================================

def test_parse_valid_card():
    """Verify successful parsing of valid flight card into FlightQuote."""
    t_date = date(2026, 9, 19)
    quote = parse_easemytrip_flight_card(SAMPLE_AKASA_CARD, "DEL", "BOM", t_date)
    assert quote is not None
    assert quote.airline == "Akasa Air"
    assert quote.flight_number == "QP 1110"
    assert quote.origin == "DEL"
    assert quote.destination == "BOM"
    assert quote.travel_date == t_date
    assert quote.departure_time == time(10, 30)
    assert quote.arrival_time == time(12, 45)
    assert quote.stops == 0
    assert quote.total_fare == Decimal("6530.00")
    assert quote.currency == "INR"
    assert quote.fare_family == "SAVER"
    assert quote.availability is True
    assert quote.source == "easemytrip_ota"


def test_malformed_card_missing_times():
    """Verify rejection when flight times cannot be determined."""
    bad_card = dict(SAMPLE_AKASA_CARD, departure_time="", arrival_time="")
    t_date = date(2026, 9, 19)
    quote = parse_easemytrip_flight_card(bad_card, "DEL", "BOM", t_date)
    assert quote is None


def test_missing_fare_rejected():
    """Verify rejection when fare is absent or below valid domestic threshold."""
    bad_card = dict(SAMPLE_AKASA_CARD, price="")
    t_date = date(2026, 9, 19)
    quote = parse_easemytrip_flight_card(bad_card, "DEL", "BOM", t_date)
    assert quote is None

    low_fare_card = dict(SAMPLE_AKASA_CARD, price="320")  # e.g. Lock price only
    assert parse_easemytrip_flight_card(low_fare_card, "DEL", "BOM", t_date) is None


def test_connecting_flight_handling():
    """Verify multi-segment connecting flights map first departure, last arrival, and stops."""
    t_date = date(2026, 9, 19)
    quote = parse_easemytrip_flight_card(SAMPLE_CONNECTING_CARD, "DEL", "BOM", t_date)
    assert quote is not None
    assert quote.airline == "Air India Express"
    assert quote.flight_number == "IX 1165/1027"
    assert quote.departure_time == time(5, 45)
    assert quote.arrival_time == time(11, 5)
    assert quote.stops == 1
    assert quote.total_fare == Decimal("6529.00")


def test_empty_results():
    """Verify that collector handles empty raw cards gracefully."""
    collector = EaseMyTripCollector.__new__(EaseMyTripCollector)
    collector.source_name = "easemytrip_ota"
    collector.page = None
    # Test extract_quotes error when uninitialized
    with pytest.raises(RuntimeError):
        collector.extract_quotes("DEL", "BOM", date(2026, 9, 19))


def test_flight_quote_pydantic_validation():
    """Verify that constructed quote passes all Pydantic FlightQuote constraints."""
    t_date = date(2026, 9, 19)
    quote = parse_easemytrip_flight_card(SAMPLE_INDIGO_CARD, "DEL", "BOM", t_date)
    assert isinstance(quote, FlightQuote)
    # Origin and destination must be 3-character uppercase
    assert len(quote.origin) == 3 and quote.origin.isupper()
    assert len(quote.destination) == 3 and quote.destination.isupper()
    # Fare must be Decimal > 0
    assert quote.total_fare > Decimal("0.00")


def test_fingerprint_compatibility():
    """Verify that FlightQuotes from EaseMyTrip integrate with compute_flight_fingerprint."""
    t_date = date(2026, 9, 19)
    quote = parse_easemytrip_flight_card(SAMPLE_AKASA_CARD, "DEL", "BOM", t_date)
    assert quote is not None
    fp = compute_flight_fingerprint(quote)
    assert isinstance(fp, str)
    assert len(fp) == 64
    # Determinism check
    fp2 = compute_flight_fingerprint(quote)
    assert fp == fp2


# ==============================================================================
# 5. Regression Tests: Generic Date Validation & City Aliases
# ==============================================================================

@pytest.mark.parametrize(
    "test_date,page_snippet",
    [
        (date(2026, 9, 17), "New Delhi - Mumbai IndiGo · Thu-17Sep2026 · Departure at 10:30"),
        (date(2026, 9, 17), "New Delhi → Mumbai | 17 Sept 2026 [1 Adult]"),
        (date(2026, 9, 23), "New Delhi - Mumbai IndiGo · Wed-23Sep2026 · Departure at 17:15"),
        (date(2026, 9, 23), "New Delhi → Mumbai | 23/09/2026"),
        (date(2026, 9, 30), "New Delhi - Mumbai Air India · Wed-30Sep2026 · Departure at 08:00"),
        (date(2026, 9, 30), "New Delhi → Mumbai | 30-Sep-2026"),
        (date(2026, 10, 1), "New Delhi - Mumbai SpiceJet · Thu-01Oct2026 · Departure at 19:55"),
        (date(2026, 10, 1), "New Delhi → Mumbai | 01 Oct 2026 [1 Adult]"),
        (date(2026, 10, 1), "New Delhi → Mumbai | 1 October 2026"),
        (date(2026, 10, 1), "New Delhi → Mumbai | 01/10/2026"),
        (date(2026, 10, 16), "New Delhi - Mumbai IndiGo · Fri-16Oct2026 · Departure at 14:00"),
        (date(2026, 10, 16), "New Delhi → Mumbai | 16-Oct-2026"),
        (date(2026, 10, 31), "New Delhi - Mumbai AkasaAir · Sat-31Oct2026 · Departure at 22:30"),
        (date(2026, 10, 31), "New Delhi → Mumbai | 31 Oct, 2026"),
        (date(2026, 12, 31), "New Delhi - Mumbai IndiGo · Thu-31Dec2026 · Departure at 12:00"),
        (date(2027, 1, 1), "New Delhi - Mumbai IndiGo · Fri-01Jan2027 · Departure at 06:00"),
    ],
)
def test_generic_date_validation_across_months_and_boundaries(test_date, page_snippet):
    """Verify generic date validation correctly verifies September, October, and year boundaries."""
    is_valid, msg = verify_travel_date_rendered(page_snippet, test_date)
    assert is_valid is True
    assert "verified" in msg.lower()


def test_date_validation_rejects_deliberately_incorrect_dates():
    """Verify date validation strictly rejects mismatched dates across all representations."""
    target = date(2026, 10, 1)

    # Page contains adjacent next day
    wrong_next_day = "New Delhi - Mumbai SpiceJet · Fri-02Oct2026 · Departure at 19:55"
    assert verify_travel_date_rendered(wrong_next_day, target)[0] is False

    # Page contains adjacent previous day
    wrong_prev_day = "New Delhi - Mumbai SpiceJet · Wed-30Sep2026 · Departure at 19:55"
    assert verify_travel_date_rendered(wrong_prev_day, target)[0] is False

    # Page contains same day in different month
    wrong_month = "New Delhi - Mumbai SpiceJet · Sun-01Nov2026 · Departure at 19:55"
    assert verify_travel_date_rendered(wrong_month, target)[0] is False

    # Page contains copyright/generic year only without full target date
    generic_text = "EaseMyTrip Flight Search © 2026 All Rights Reserved"
    assert verify_travel_date_rendered(generic_text, target)[0] is False


def test_city_aliases_validation_blr():
    """Verify BLR accepts canonical code, 'Bengaluru' (portal representation), and 'Bangalore'."""
    assert verify_route_rendered("Bengaluru to New Delhi Flights | Wed-23Sep2026", "BLR", "DEL")[0] is True
    assert verify_route_rendered("Bangalore to Delhi Flights | Wed-23Sep2026", "BLR", "DEL")[0] is True
    assert verify_route_rendered("BLR to DEL Flights | Wed-23Sep2026", "BLR", "DEL")[0] is True


def test_city_aliases_validation_hyd():
    """Verify HYD accepts 'Hyderabad', 'Hyderbad' (portal typo representation), and 'HYD'."""
    assert verify_route_rendered("New Delhi - Hyderbad Air India Express", "DEL", "HYD")[0] is True
    assert verify_route_rendered("New Delhi - Hyderabad IndiGo", "DEL", "HYD")[0] is True
    assert verify_route_rendered("DEL - HYD Non-stop", "DEL", "HYD")[0] is True


def test_city_aliases_validation_ixb():
    """Verify IXB accepts 'Bagdogra' and canonical 'IXB'."""
    assert verify_route_rendered("Departure from Bagdogra Arrival at New Delhi", "IXB", "DEL")[0] is True
    assert verify_route_rendered("IXB to DEL Flights", "IXB", "DEL")[0] is True


def test_city_aliases_validation_ixl():
    """Verify IXL accepts 'Leh In', 'Leh', and canonical 'IXL'."""
    assert verify_route_rendered("New Delhi - Leh In SpiceJet", "DEL", "IXL")[0] is True
    assert verify_route_rendered("New Delhi - Leh Flights", "DEL", "IXL")[0] is True
    assert verify_route_rendered("DEL to IXL Non-stop", "DEL", "IXL")[0] is True


def test_route_validation_rejects_incorrect_or_fuzzy_routes():
    """Verify strict word-boundary token matching rejects mismatched or substring-polluted routes."""
    # Completely mismatched route
    mismatched_page = "Mumbai to Chennai Flights | Thu-01Oct2026"
    assert verify_route_rendered(mismatched_page, "BLR", "DEL")[0] is False

    # Fuzzy substring pollution rejection (e.g. 'delivery' contains 'del', but must NOT match DEL)
    polluted_text = "Fast delivery guaranteed for all airline passengers travelling to Hyderabad"
    # Origin DEL is not present, only substring in 'delivery'
    assert verify_route_rendered(polluted_text, "DEL", "HYD")[0] is False


def test_airport_compatibility_with_aliases_and_secondary_filter():
    """Verify station compatibility accepts aliases while maintaining secondary airport exclusions."""
    # BLR aliases
    assert is_airport_compatible("Bengaluru International Airport", "BLR") is True
    assert is_airport_compatible("Bangalore Airport", "BLR") is True

    # IXB, IXL, HYD
    assert is_airport_compatible("Bagdogra Airport", "IXB") is True
    assert is_airport_compatible("Leh Airport", "IXL") is True
    assert is_airport_compatible("Hyderbad Rajiv Gandhi Airport", "HYD") is True
    assert is_airport_compatible("Hyderabad", "HYD") is True

    # Secondary airport exclusions remain strictly enforced
    assert is_airport_compatible("Hindon Airport (Ghaziabad)", "DEL") is False
    assert is_airport_compatible("Navi Mumbai International Airport", "BOM") is False
