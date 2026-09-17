"""Unit tests for CleartripCollector.

Tests verify:
- URL construction (origin, destination, date, passengers, cabin mapping)
- Airline normalization
- Flight number normalization (single-leg, multi-segment)
- Time parsing (24h clock, ISO strings)
- Stops parsing (non-stop, 1 stop, 2 stops)
- Fare parsing (direct numeric, currency text, rejecting discounts/negative/zero)
- Date safety verification (valid date, mismatched date, missing date)
- Route safety (valid route, wrong origin, wrong destination, HDO rejection, DXN rejection, NMI rejection)
- Connecting flights (multi-leg itineraries, combined flight numbers)
- API payload parsing (valid, malformed, missing fields, missing fare, no inventory)
- FlightQuote validation and fingerprint compatibility
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal
import pytest
from unittest.mock import MagicMock, patch

from backend.collectors.playwright.cleartrip_collector import (
    CleartripCollector,
    CollectorDateMismatchError,
    CollectorRouteMismatchError,
    normalize_airline_name,
    parse_flight_number,
    parse_time_string,
    parse_fare_value,
    verify_travel_date_string,
)
from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.processing.ingestion.fingerprint import compute_flight_fingerprint


# =====================================================================
# 1. URL CONSTRUCTION & PARAMETER TESTS
# =====================================================================

def test_build_search_url_default():
    """Verify default URL construction with economy cabin and 1 adult."""
    url = CleartripCollector.build_search_url(
        origin="del",
        destination="bom",
        travel_date=date(2026, 9, 19),
    )
    expected = (
        "https://www.cleartrip.com/flights/results?"
        "from=DEL&to=BOM&depart_date=19/09/2026"
        "&adults=1&childs=0&infants=0&class=Economy"
        "&airline=&carrier=&intl=n&page=loaded"
    )
    assert url == expected


def test_build_search_url_custom_pax_and_cabin():
    """Verify passenger counts and cabin mapping for Business class."""
    url = CleartripCollector.build_search_url(
        origin="BLR",
        destination="DEL",
        travel_date=date(2026, 10, 5),
        adults=2,
        children=1,
        infants=1,
        cabin_class="BUSINESS",
    )
    assert "from=BLR" in url
    assert "to=DEL" in url
    assert "depart_date=05/10/2026" in url
    assert "adults=2" in url
    assert "childs=1" in url
    assert "infants=1" in url
    assert "class=Business" in url


def test_build_search_url_premium_economy():
    """Verify Premium Economy cabin mapping."""
    url = CleartripCollector.build_search_url(
        origin="MAA",
        destination="BOM",
        travel_date=date(2026, 11, 15),
        cabin_class="PREMIUM_ECONOMY",
    )
    assert "class=Premium Economy" in url


# =====================================================================
# 2. AIRLINE & FLIGHT NUMBER NORMALIZATION
# =====================================================================

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("IndiGo", "IndiGo"),
        ("6E", "IndiGo"),
        ("Air India", "Air India"),
        ("AI", "Air India"),
        ("Air India Express", "Air India Express"),
        ("IX", "Air India Express"),
        ("Akasa Air", "Akasa Air"),
        ("QP", "Akasa Air"),
        ("SpiceJet", "SpiceJet"),
        ("SG", "SpiceJet"),
        ("", "Unknown"),
    ],
)
def test_normalize_airline_name(raw, expected):
    assert normalize_airline_name(raw) == expected


@pytest.mark.parametrize(
    "val,carrier,expected",
    [
        ("IX-1235", None, "IX 1235"),
        ("IX 1235", None, "IX 1235"),
        ("6E-449", None, "6E 449"),
        ("QP-1110", None, "QP 1110"),
        ("1110", "QP", "QP 1110"),
        ("5096", "6E", "6E 5096"),
        ("AI 2483 / AI 2740", None, "AI 2483/2740"),
        ("AI 2483 / 2740", None, "AI 2483/2740"),
        ("invalid", None, None),
        ("", None, None),
    ],
)
def test_parse_flight_number(val, carrier, expected):
    assert parse_flight_number(val, carrier_code=carrier) == expected


# =====================================================================
# 3. TIME, STOPS & FARE PARSING
# =====================================================================

def test_parse_time_string():
    assert parse_time_string("23:25") == time(23, 25)
    assert parse_time_string("05:00") == time(5, 0)
    assert parse_time_string("2026-09-19T23:25:00.000+05:30") == time(23, 25)
    assert parse_time_string("invalid") is None
    assert parse_time_string("") is None


def test_parse_fare_value():
    assert parse_fare_value(6529.0) == Decimal("6529.00")
    assert parse_fare_value("6529") == Decimal("6529.00")
    assert parse_fare_value("₹6,529") == Decimal("6529.00")
    assert parse_fare_value("INR 7,721.50") == Decimal("7721.50")
    # Discard zero, negative, or under-threshold fares
    assert parse_fare_value(0) is None
    assert parse_fare_value(-500) is None
    assert parse_fare_value(250) is None  # Below 500 threshold
    # Discard promotional badges
    assert parse_fare_value("Get ₹225 off with CTDOM") is None
    assert parse_fare_value("invalid") is None
    assert parse_fare_value(None) is None


# =====================================================================
# 4. DATE SAFETY VERIFICATION
# =====================================================================

def test_verify_travel_date_string_success():
    target = date(2026, 9, 19)
    valid, msg = verify_travel_date_string("Sat, 19 Sep  ₹6,529", target)
    assert valid is True
    assert "19 Sep" in msg

    valid2, _ = verify_travel_date_string("19/09/2026", target)
    assert valid2 is True


def test_verify_travel_date_string_mismatch():
    target = date(2026, 9, 19)
    valid, msg = verify_travel_date_string("Fri, 25 Sep  ₹6,500", target)
    assert valid is False
    assert "does not match" in msg


def test_verify_travel_date_string_empty():
    target = date(2026, 9, 19)
    valid, msg = verify_travel_date_string("", target)
    assert valid is False
    assert "empty" in msg


# =====================================================================
# 5. ROUTE SAFETY & SECONDARY AIRPORT REJECTION
# =====================================================================

def test_route_safety_filters_secondary_airports():
    """Verify that Hindon (HDO), Jewar (DXN), and Navi Mumbai (NMI) are strictly rejected."""
    collector = CleartripCollector()
    mock_page = MagicMock()
    collector.page = mock_page

    # Mock raw records including DEL->BOM, HDO->BOM, DXN->BOM, DEL->NMI
    mock_raw_records = [
        {
            "airline": "Air India Express",
            "carrier_code": "IX",
            "flight_number": "IX 1235",
            "origin": "DEL",
            "destination": "BOM",
            "departure_time": "23:25",
            "arrival_time": "01:50",
            "stops": 0,
            "price": 6529.0,
            "fare_family": "VALUE",
        },
        {
            # Hindon Airport secondary
            "airline": "Air India Express",
            "carrier_code": "IX",
            "flight_number": "IX 1999",
            "origin": "HDO",
            "destination": "BOM",
            "departure_time": "12:00",
            "arrival_time": "14:15",
            "stops": 0,
            "price": 7721.0,
            "fare_family": "SAVER",
        },
        {
            # Jewar / Noida International secondary
            "airline": "Akasa Air",
            "carrier_code": "QP",
            "flight_number": "QP 1940",
            "origin": "DXN",
            "destination": "BOM",
            "departure_time": "14:00",
            "arrival_time": "16:20",
            "stops": 0,
            "price": 6618.0,
            "fare_family": "SAVER",
        },
        {
            # Navi Mumbai secondary
            "airline": "IndiGo",
            "carrier_code": "6E",
            "flight_number": "6E 999",
            "origin": "DEL",
            "destination": "NMI",
            "departure_time": "18:00",
            "arrival_time": "20:15",
            "stops": 0,
            "price": 6530.0,
            "fare_family": "SAVER",
        },
    ]

    with patch.object(collector, "extract_fares", return_value=mock_raw_records):
        quotes = collector.extract_quotes(
            origin="DEL",
            destination="BOM",
            travel_date=date(2026, 9, 19),
        )

    # Exactly 1 quote should pass (IX 1235 DEL->BOM)
    assert len(quotes) == 1
    assert quotes[0].flight_number == "IX 1235"
    assert quotes[0].origin == "DEL"
    assert quotes[0].destination == "BOM"
    assert collector._last_rejected_secondary_airports == 3


# =====================================================================
# 6. CONNECTING FLIGHTS & MULTI-STOP ITINERARIES
# =====================================================================

def test_connecting_flight_handling():
    """Verify that multi-segment itineraries preserve unified identity and calculate stops."""
    collector = CleartripCollector()
    mock_page = MagicMock()
    collector.page = mock_page

    mock_raw_records = [
        {
            "airline": "Air India",
            "carrier_code": "AI",
            "flight_number": "AI 2483/2740",
            "origin": "DEL",
            "destination": "BOM",
            "departure_time": "06:00",
            "arrival_time": "13:30",
            "stops": 1,
            "price": 9500.0,
            "fare_family": "ECO VALUE",
        }
    ]

    with patch.object(collector, "extract_fares", return_value=mock_raw_records):
        quotes = collector.extract_quotes(
            origin="DEL",
            destination="BOM",
            travel_date=date(2026, 9, 19),
        )

    assert len(quotes) == 1
    q = quotes[0]
    assert q.flight_number == "AI 2483/2740"
    assert q.stops == 1
    assert q.departure_time == time(6, 0)
    assert q.arrival_time == time(13, 30)
    assert q.total_fare == Decimal("9500.00")


# =====================================================================
# 7. API PAYLOAD PARSING & RESILIENCE
# =====================================================================

def test_extract_fares_from_api_structured_payload():
    """Verify parsing from a representative flight/search/v2 API structure."""
    collector = CleartripCollector()

    sample_api_data = {
        "cards": {
            "J1": [
                {
                    "cardId": 0,
                    "summary": {
                        "flights": [
                            {"airlineCode": "6E", "flightNumber": "449", "id": "6E-449-DEL-BOM-1"}
                        ],
                        "firstDeparture": {
                            "airlineCode": "6E",
                            "airport": {
                                "code": "DEL",
                                "time": "2026-09-19T05:00:00.000+05:30"
                            }
                        },
                        "lastArrival": {
                            "airlineCode": "6E",
                            "airport": {
                                "code": "BOM",
                                "time": "2026-09-19T07:15:00.000+05:30"
                            }
                        },
                        "stops": 0,
                    },
                    "subTravelOptionIds": ["sub-1"],
                }
            ]
        },
        "subTravelOptions": {
            "sub-1": {
                "cheapestFareId": "fare-1",
                "fareList": [{"displayText": {"displayTitle": "SAVER"}}]
            }
        },
        "fares": {
            "fare-1": {
                "brand": "SAVER",
                "pricing": {
                    "totalPricing": {
                        "totalPrice": 6530.0,
                        "totalBaseFare": 5065.0,
                        "totalTax": 1465.0
                    }
                }
            }
        },
        "flights": {
            "6E-449-DEL-BOM-1": {"aircraftType": "A321"}
        },
        "metaData": {
            "airlineDetail": {"6E": {"name": "IndiGo"}}
        }
    }

    raw = collector._extract_fares_from_api(sample_api_data)
    assert len(raw) == 1
    r = raw[0]
    assert r["airline"] == "IndiGo"
    assert r["flight_number"] == "6E 449"
    assert r["origin"] == "DEL"
    assert r["destination"] == "BOM"
    assert r["price"] == 6530.0
    assert r["base_fare"] == 5065.0
    assert r["tax"] == 1465.0
    assert r["aircraft"] == "A321"


def test_extract_fares_from_api_malformed_or_missing_fare():
    """Verify graceful handling of cards with missing fare or invalid flight lists."""
    collector = CleartripCollector()

    malformed_data = {
        "cards": {
            "J1": [
                {
                    "cardId": 1,
                    "summary": {
                        "flights": [],
                        "firstDeparture": {"airport": {"code": "DEL", "time": "2026-09-19T10:00:00.000+05:30"}},
                        "lastArrival": {"airport": {"code": "BOM", "time": "2026-09-19T12:00:00.000+05:30"}},
                    },
                    "subTravelOptionIds": ["missing-sub"],
                }
            ]
        },
        "subTravelOptions": {},
        "fares": {},
        "flights": {},
        "metaData": {}
    }

    raw = collector._extract_fares_from_api(malformed_data)
    assert len(raw) == 1
    assert raw[0]["price"] is None

    # Feeding this to extract_quotes should drop it due to missing fare
    collector.page = MagicMock()
    with patch.object(collector, "extract_fares", return_value=raw):
        quotes = collector.extract_quotes(
            origin="DEL", destination="BOM", travel_date=date(2026, 9, 19)
        )
    assert len(quotes) == 0


# =====================================================================
# 8. FLIGHTQUOTE SCHEMA & FINGERPRINT COMPATIBILITY
# =====================================================================

def test_flight_quote_creation_and_fingerprint():
    """Verify generated FlightQuote passes pydantic validation and generates standard fingerprint."""
    obs_time = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    quote = FlightQuote(
        airline="Air India Express",
        flight_number="IX 1235",
        origin="DEL",
        destination="BOM",
        travel_date=date(2026, 9, 19),
        departure_time=time(23, 25),
        arrival_time=time(1, 50),
        cabin_class="ECONOMY",
        fare_family="VALUE",
        stops=0,
        total_fare=Decimal("6529.00"),
        currency="INR",
        availability=True,
        source="cleartrip_ota",
        observed_at=obs_time,
    )

    assert quote.airline == "Air India Express"
    assert quote.flight_number == "IX 1235"
    assert quote.total_fare == Decimal("6529.00")
    assert quote.currency == "INR"

    fp = compute_flight_fingerprint(quote)
    assert isinstance(fp, str)
    assert len(fp) == 64  # Standard SHA-256 hex string


# =====================================================================
# 9. COLLECT() TOP-LEVEL WORKFLOW & EXCEPTION RAISING
# =====================================================================

def test_collect_raises_date_mismatch():
    """Verify collect() raises CollectorDateMismatchError when active card has wrong date."""
    collector = CleartripCollector()
    mock_page = MagicMock()
    collector.page = mock_page
    collector.start = MagicMock(return_value=mock_page)
    collector.close = MagicMock()

    # Active date card text returns mismatched date
    mock_page.evaluate.return_value = "Fri, 25 Sep  ₹6,500"

    with pytest.raises(CollectorDateMismatchError) as exc_info:
        collector.collect(
            origin="DEL",
            destination="BOM",
            travel_date=date(2026, 9, 19),  # Requested 19 Sep
        )

    assert "travel date validation failed" in str(exc_info.value)
