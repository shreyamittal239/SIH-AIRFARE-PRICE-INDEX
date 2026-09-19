"""EaseMyTrip online travel aggregator (OTA) flight data collector.

Automates flight searches on EaseMyTrip (https://flight.easemytrip.com), supporting
canonical parameterized search URL navigation, verifying travel-date and route consistency
against rendered DOM elements, and extracting normalized FlightQuote records conforming to
the project schema.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from playwright.sync_api import Page, Error as PlaywrightError

from backend.collectors.playwright.base_collector import BaseCollector
from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.schemas.flight_quote import FlightQuote

logger = logging.getLogger("easemytrip_collector")


class CollectorDateMismatchError(ValueError):
    """Raised when requested travel date disagrees with the rendered DOM travel date."""
    pass


class CollectorRouteMismatchError(ValueError):
    """Raised when requested origin/destination route disagrees with rendered DOM results."""
    pass


# Known airport code to city name mapping used in EaseMyTrip parameterized URLs
CITY_NAME_MAP = {
    "DEL": "Delhi",
    "BOM": "Mumbai",
    "BLR": "Bangalore",
    "MAA": "Chennai",
    "CCU": "Kolkata",
    "HYD": "Hyderabad",
    "GOI": "Goa",
    "GOX": "Goa",
    "PNQ": "Pune",
    "AMD": "Ahmedabad",
    "COK": "Kochi",
    "JAI": "Jaipur",
    "LKO": "Lucknow",
    "PAT": "Patna",
    "SXR": "Srinagar",
    "IXC": "Chandigarh",
    "GAU": "Guwahati",
    "BBI": "Bhubaneswar",
    "IXB": "Bagdogra",
    "IXL": "Leh",
}

# Explicit mapping from canonical 3-letter IATA code to accepted EaseMyTrip city name representations
EASEMYTRIP_CITY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "DEL": ("Delhi", "New Delhi"),
    "BOM": ("Mumbai",),
    "BLR": ("Bengaluru", "Bangalore"),
    "MAA": ("Chennai",),
    "CCU": ("Kolkata",),
    "HYD": ("Hyderabad", "Hyderbad"),
    "GOI": ("Goa",),
    "GOX": ("Goa",),
    "PNQ": ("Pune",),
    "AMD": ("Ahmedabad",),
    "COK": ("Kochi", "Cochin"),
    "JAI": ("Jaipur",),
    "LKO": ("Lucknow",),
    "PAT": ("Patna",),
    "SXR": ("Srinagar",),
    "IXC": ("Chandigarh",),
    "GAU": ("Guwahati",),
    "BBI": ("Bhubaneswar",),
    "IXB": ("Bagdogra",),
    "IXL": ("Leh In", "Leh"),
}

# Normalization mapping for English month names and abbreviations to integer (1-12)
MONTH_NAME_TO_INT: Dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Cabin class code mapping for EaseMyTrip
CABIN_CODE_MAP = {
    "ECONOMY": 0,
    "PREMIUM_ECONOMY": 1,
    "BUSINESS": 2,
    "FIRST": 3,
}

# Known airline display names to canonical normalization
AIRLINE_NORMALIZATION_MAP = {
    "AKASAAIR": "Akasa Air",
    "AKASA AIR": "Akasa Air",
    "INDIGO": "IndiGo",
    "AIR INDIA": "Air India",
    "AIR INDIA EXPRESS": "Air India Express",
    "SPICEJET": "SpiceJet",
    "VISTARA": "Vistara",
    "ALLIANCE AIR": "Alliance Air",
    "STAR AIR": "Star Air",
    "FLY91": "Fly91",
}


def parse_time_string(val: str) -> Optional[time]:
    """Parse time string like '10:30' or '05:45' into datetime.time."""
    if not val:
        return None
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", val.strip())
    if match:
        try:
            return time(int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    return None


def parse_fare_string(val: str) -> Optional[Decimal]:
    """Parse currency string like '₹ 6,530', '6,530', or '6530.00' into normalized Decimal.

    Rejects time strings (containing ':'), promotional discount notices ('₹7500 OFF'),
    lock price notices ('Lock Price ₹ 327'), and non-currency text.
    """
    if not val:
        return None
    clean_val = val.strip()

    # Reject times (e.g. 05:45)
    if ":" in clean_val:
        return None

    lower = clean_val.lower()
    # Reject promotional discount badges, lock price notices, fee notices
    if any(badge in lower for badge in ["off", "save", "discount", "lock", "coupon", "code", "starts at", "cash"]):
        return None

    # Strip currency prefixes (e.g. Rs, Rs., INR, ₹)
    stripped = re.sub(r"^(?:Rs\.?|INR|₹)\s*", "", clean_val, flags=re.IGNORECASE).strip()

    # Reject non-currency words (e.g. 'View Fare', 'Book Now')
    if re.search(r"[A-Za-z]", stripped):
        return None

    # Match numeric currency pattern: e.g. 6,530 or 6530 or 12,499.00
    match = re.search(r"(\d[\d,]*(?:\.\d{1,2})?)", stripped)
    if match:
        num_str = match.group(1).replace(",", "")
        try:
            val_dec = Decimal(num_str).quantize(Decimal("0.01"))
            # Standard domestic fare threshold to avoid small add-on fees or lock price artifacts
            if val_dec >= Decimal("500.00"):
                return val_dec
        except Exception:
            return None
    return None


def parse_flight_number(val: str) -> Optional[str]:
    """Normalize EaseMyTrip flight numbers.

    Examples:
      'QP-1110' -> 'QP 1110'
      '6E-5096' -> '6E 5096'
      'IX-1235' -> 'IX 1235'
      'AI-887'  -> 'AI 887'
      'SG-9091' -> 'SG 9091'
      'IX-1165/1027' -> 'IX 1165/1027'
    """
    if not val:
        return None
    clean = val.strip().replace("-", " ")
    clean = re.sub(r"\s+", " ", clean)
    match = re.search(r"\b([A-Z0-9]{2,3}\s+\d{3,4}(?:/\d{3,4})?)\b", clean, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def normalize_airline_name(raw_name: str) -> str:
    """Normalize airline name string to canonical project format."""
    if not raw_name:
        return "Unknown"
    clean = raw_name.strip()
    clean_upper = clean.upper()
    return AIRLINE_NORMALIZATION_MAP.get(clean_upper, clean)


def parse_stops_string(val: str) -> int:
    """Parse stops descriptor from card (e.g. 'Non-stop' -> 0, '1-stop' -> 1, '2-stop' -> 2)."""
    if not val:
        return 0
    lower = val.strip().lower()
    if "non-stop" in lower or "non stop" in lower or "direct" in lower:
        return 0
    match = re.search(r"\b(\d+)\s*(?:-| )?stop", lower)
    if match:
        return int(match.group(1))
    if "1" in lower:
        return 1
    if "2" in lower:
        return 2
    return 0


def extract_rendered_dates(page_text: str) -> List[date]:
    """Extract and parse all structured date representations from EaseMyTrip page text.

    Supports formats rendered across EaseMyTrip headers, cards, and summaries:
      - '19 Sept 2026', '19 Sep 2026', '01 Oct 2026', '1 Oct 2026', '1 October 2026'
      - 'Thu-01Oct2026', 'Sat-19Sep2026'
      - '19-Sep-2026', '01-Oct-2026', '1-Oct-2026'
      - '19Sep2026', '01Oct2026'
      - '19/09/2026', '01/10/2026'
      - '2026-10-01', '2026/10/01'
    """
    if not page_text:
        return []

    found_dates: List[date] = []

    # 1. Day Month Year with spaces or commas (e.g. '19 Sept 2026', '01 Oct 2026', '1 Oct, 2026')
    for match in re.finditer(
        r"\b(\d{1,2})\s+([A-Za-z]{3,9}),?\s+(\d{4})\b", page_text
    ):
        day_str, m_str, y_str = match.groups()
        m_int = MONTH_NAME_TO_INT.get(m_str.lower())
        if m_int:
            try:
                found_dates.append(date(int(y_str), m_int, int(day_str)))
            except ValueError:
                pass

    # 2. Card hyphenated/concatenated dates (e.g. 'Thu-01Oct2026', '19Sep2026', '01Oct2026', '1-Oct-2026')
    for match in re.finditer(
        r"(?:[A-Za-z]{3}-)?(\d{1,2})-?([A-Za-z]{3,4})-?(\d{4})\b", page_text
    ):
        day_str, m_str, y_str = match.groups()
        m_int = MONTH_NAME_TO_INT.get(m_str.lower())
        if m_int:
            try:
                found_dates.append(date(int(y_str), m_int, int(day_str)))
            except ValueError:
                pass

    # 3. Numeric slash format (e.g. '19/09/2026', '01/10/2026')
    for match in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", page_text):
        d_str, m_str, y_str = match.groups()
        try:
            found_dates.append(date(int(y_str), int(m_str), int(d_str)))
        except ValueError:
            pass

    # 4. ISO formatted date (e.g. '2026-10-01', '2026/10/01')
    for match in re.finditer(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", page_text):
        y_str, m_str, d_str = match.groups()
        try:
            found_dates.append(date(int(y_str), int(m_str), int(d_str)))
        except ValueError:
            pass

    return list(dict.fromkeys(found_dates))


def verify_travel_date_rendered(page_text: str, target_date: date) -> Tuple[bool, str]:
    """Verify that the rendered EaseMyTrip DOM corresponds to the requested travel date.

    Generic across all months and years. Extracts structured date strings from
    the DOM, normalizes them into datetime.date objects, and validates that
    the authoritative target_date is present.

    Args:
        page_text: Full text content of the rendered page.
        target_date: Authoritative target departure date.

    Returns:
        Tuple of (is_valid: bool, reason_message: str)
    """
    if not page_text:
        return False, "Empty page text"

    # Explicit no-flights check
    if "no flights found" in page_text.lower() or "sorry, no flights" in page_text.lower():
        return False, "No inventory available on requested date (no flights banner rendered)"

    rendered_dates = extract_rendered_dates(page_text)
    if target_date in rendered_dates:
        return True, "Target travel date verified in rendered DOM"

    return False, f"Rendered page does not contain target date '{target_date.strftime('%d %b %Y')}'"


def _matches_city_token(page_text: str, airport_code: str) -> bool:
    """Check whether page_text contains accepted city or airport tokens for an IATA code.

    Enforces strict whole-token word boundary matching to avoid fuzzy false positives.
    """
    code_upper = airport_code.strip().upper()
    # Check exact uppercase IATA code with word boundary (e.g. \bDEL\b, \bBLR\b)
    if re.search(rf"\b{re.escape(code_upper)}\b", page_text):
        return True

    # Check accepted city name aliases with word boundaries (e.g. \bBengaluru\b, \bHyderbad\b)
    aliases = EASEMYTRIP_CITY_ALIASES.get(code_upper, ())
    for alias in aliases:
        if re.search(rf"\b{re.escape(alias)}\b", page_text, re.IGNORECASE):
            return True

    # Fallback to CITY_NAME_MAP if alias table lacks it
    fallback = CITY_NAME_MAP.get(code_upper)
    if fallback and re.search(rf"\b{re.escape(fallback)}\b", page_text, re.IGNORECASE):
        return True

    return False


def verify_route_rendered(page_text: str, origin: str, destination: str) -> Tuple[bool, str]:
    """Verify that the rendered page header represents the requested origin and destination.

    Enforces canonical IATA code resolution through accepted website-specific aliases
    without fuzzy substring matching.
    """
    if not page_text:
        return False, "Empty page text"

    has_origin = _matches_city_token(page_text, origin)
    has_dest = _matches_city_token(page_text, destination)

    if not (has_origin and has_dest):
        orig_city = CITY_NAME_MAP.get(origin.upper(), origin.upper())
        dest_city = CITY_NAME_MAP.get(destination.upper(), destination.upper())
        return False, f"Rendered page does not reflect route {origin} ({orig_city}) -> {destination} ({dest_city})"

    return True, "Route verified in rendered DOM"


def is_airport_compatible(airport_name: str, expected_code: str) -> bool:
    """Verify that an individual card's station corresponds to the expected metropolitan hub.

    Guards against airport mismatches such as:
      - DEL (Indira Gandhi / New Delhi) vs Hindon / Ghaziabad (HDO)
      - BOM (CSMIA / Mumbai) vs Navi Mumbai (NMI)
    """
    clean = airport_name.strip().lower()
    if expected_code == "DEL":
        # Disallow secondary / regional airports in Delhi NCR if DEL was requested
        if any(bad in clean for bad in ["ghaziabad", "hindon", "jewar", "noida"]):
            return False
        return any(good in clean for good in ["delhi", "new delhi", "del", "igi", "indira gandhi"])

    if expected_code == "BOM":
        # Disallow Navi Mumbai (NMI) if BOM was requested
        if any(bad in clean for bad in ["navi mumbai", "panvel"]):
            return False
        return any(good in clean for good in ["mumbai", "bom", "csmia"])

    # Check accepted aliases
    aliases = EASEMYTRIP_CITY_ALIASES.get(expected_code.upper(), ())
    for alias in aliases:
        if alias.lower() in clean:
            return True

    # Fallback to city name or code match
    expected_city = CITY_NAME_MAP.get(expected_code, expected_code).lower()
    return expected_code.lower() in clean or expected_city in clean



def parse_easemytrip_flight_card(
    card: Dict[str, Any],
    origin: str,
    destination: str,
    travel_date: date,
    cabin_class: str = "ECONOMY",
    source_name: str = "easemytrip_ota",
    observed_at: Optional[datetime] = None,
) -> Optional[FlightQuote]:
    """Pure parser: converts extracted dictionary data of an EaseMyTrip flight card into a FlightQuote.

    Args:
        card: Dictionary containing extracted field values from the DOM element.
        origin: Requested origin airport code (e.g. 'DEL').
        destination: Requested destination airport code (e.g. 'BOM').
        travel_date: Requested departure date.
        cabin_class: Requested cabin class (default 'ECONOMY').
        source_name: Data source identifier (default 'easemytrip_ota').
        observed_at: Timestamp of observation.

    Returns:
        FlightQuote instance or None if required fields are missing or invalid.
    """
    raw_airline = card.get("airline", "")
    raw_flight_number = card.get("flight_number", "")
    raw_dep_time = card.get("departure_time", "")
    raw_arr_time = card.get("arrival_time", "")
    raw_dep_airport = card.get("departure_airport", "")
    raw_arr_airport = card.get("arrival_airport", "")
    raw_stops = card.get("stops", "")
    raw_price = card.get("price", "")

    # 1. Airport Compatibility Filter (e.g. exclude Hindon/Navi Mumbai if DEL/BOM requested)
    if raw_dep_airport and not is_airport_compatible(raw_dep_airport, origin.upper()):
        logger.debug("Skipping card: departure airport '%s' does not match origin '%s'", raw_dep_airport, origin)
        return None
    if raw_arr_airport and not is_airport_compatible(raw_arr_airport, destination.upper()):
        logger.debug("Skipping card: arrival airport '%s' does not match destination '%s'", raw_arr_airport, destination)
        return None

    # 2. Airline Normalization
    airline = normalize_airline_name(raw_airline)
    if not airline or airline == "Unknown":
        return None

    # 3. Flight Number Normalization
    flight_number = parse_flight_number(raw_flight_number)
    if not flight_number:
        return None

    # 4. Times
    departure_time = parse_time_string(raw_dep_time)
    arrival_time = parse_time_string(raw_arr_time)
    if departure_time is None or arrival_time is None:
        return None

    # 5. Stops
    stops = parse_stops_string(raw_stops)

    # 6. Total Fare (prefer direct numeric price, fallback to parsing display string)
    total_fare: Optional[Decimal] = None
    if raw_price:
        if isinstance(raw_price, (int, float, Decimal)):
            total_fare = Decimal(str(raw_price)).quantize(Decimal("0.01"))
        else:
            total_fare = parse_fare_string(str(raw_price))

    if total_fare is None or total_fare < Decimal("500.00"):
        return None

    # 7. Fare Family
    fare_family = card.get("fare_family", "SAVER") or "SAVER"

    return FlightQuote(
        airline=airline,
        flight_number=flight_number,
        origin=origin.upper(),
        destination=destination.upper(),
        travel_date=travel_date,
        departure_time=departure_time,
        arrival_time=arrival_time,
        cabin_class=cabin_class.upper(),
        fare_family=fare_family.upper(),
        stops=stops,
        total_fare=total_fare,
        currency="INR",
        availability=True,
        source=source_name,
        observed_at=observed_at or datetime.now(timezone.utc),
    )


class EaseMyTripCollector(BaseCollector):
    """Playwright collector for EaseMyTrip (flight.easemytrip.com) flight searches."""

    def __init__(
        self,
        browser_manager: Optional[BrowserManager] = None,
        timeout_ms: int = 35000,
    ) -> None:
        """Initialize collector with 'easemytrip_ota' source identifier."""
        bm = browser_manager or BrowserManager(headless=True, timeout_ms=timeout_ms)
        super().__init__(source_name="easemytrip_ota", browser_manager=bm)
        self.timeout_ms = timeout_ms

    @staticmethod
    def build_search_url(
        origin: str,
        destination: str,
        travel_date: date,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        cabin_class: str = "ECONOMY",
    ) -> str:
        """Construct canonical EaseMyTrip parameterized flight search URL.

        Format:
        https://flight.easemytrip.com/FlightList/Index?srch=DEL-Delhi-India|BOM-Mumbai-India|19/09/2026&px=1-0-0&cbn=0&ar=undefined&isSplit=false
        """
        orig_clean = origin.strip().upper()
        dest_clean = destination.strip().upper()

        orig_city = CITY_NAME_MAP.get(orig_clean, orig_clean)
        dest_city = CITY_NAME_MAP.get(dest_clean, dest_clean)

        date_str = travel_date.strftime("%d/%m/%Y")
        cabin_code = CABIN_CODE_MAP.get(cabin_class.strip().upper(), 0)
        pax_str = f"{adults}-{children}-{infants}"

        return (
            f"https://flight.easemytrip.com/FlightList/Index?"
            f"srch={orig_clean}-{orig_city}-India|{dest_clean}-{dest_city}-India|{date_str}"
            f"&px={pax_str}&cbn={cabin_code}&ar=undefined&isSplit=false"
        )

    def extract_fares(self, page: Page) -> List[Dict[str, Any]]:
        """Extract raw flight card dictionaries from the loaded EaseMyTrip results page.

        Satisfies BaseCollector abstract interface contract.
        Extracts structured field data directly via DOM evaluation for maximum stability.
        """
        raw_cards = page.evaluate("""() => {
            const results = [];
            const cardElements = document.querySelectorAll('.fltResult');

            for (const card of cardElements) {
                // Airline name
                const airlineElem = card.querySelector('span.txt-r4');
                const airline = airlineElem ? airlineElem.innerText.trim() : '';

                // Flight number
                const fltElem = card.querySelector('span.txt-r5');
                const flightNumber = fltElem ? fltElem.innerText.trim() : '';

                // Times (first is dep, second is arr)
                const timeElems = card.querySelectorAll('span.txt-r2-n');
                const depTime = timeElems.length > 0 ? timeElems[0].innerText.trim() : '';
                const arrTime = timeElems.length > 1 ? timeElems[1].innerText.trim() : '';

                // Stations (first is dep, second is arr)
                const airportElems = card.querySelectorAll('div.txt-r3-n');
                const depAirport = airportElems.length > 0 ? airportElems[0].innerText.trim() : '';
                const arrAirport = airportElems.length > 1 ? airportElems[1].innerText.trim() : '';

                // Duration & Stops
                const duraElem = card.querySelector('span.dura_md');
                const duration = duraElem ? duraElem.innerText.trim() : '';
                const stopsElem = card.querySelector('span.dura_md2');
                const stops = stopsElem ? stopsElem.innerText.trim() : 'Non-stop';

                // Price (prefer attribute price on span[price], fallback to .txt-r6-n)
                let price = '';
                const priceSpan = card.querySelector('span[price]');
                if (priceSpan && priceSpan.getAttribute('price')) {
                    price = priceSpan.getAttribute('price');
                } else {
                    const prcElem = card.querySelector('.txt-r6-n');
                    if (prcElem) {
                        price = prcElem.innerText.trim();
                    }
                }

                results.push({
                    airline: airline,
                    flight_number: flightNumber,
                    departure_time: depTime,
                    arrival_time: arrTime,
                    departure_airport: depAirport,
                    arrival_airport: arrAirport,
                    duration: duration,
                    stops: stops,
                    price: price,
                    fare_family: 'SAVER',
                    raw_text: card.innerText || ''
                });
            }

            return results;
        }""")
        return raw_cards

    def extract_quotes(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        cabin_class: str = "ECONOMY",
        observed_at: Optional[datetime] = None,
    ) -> List[FlightQuote]:
        """Extract and normalize all valid flight quotes from currently loaded EaseMyTrip page."""
        if self.page is None:
            raise RuntimeError("Page is not initialized. Call start() or collect() first.")

        raw_cards = self.extract_fares(self.page)
        quotes: List[FlightQuote] = []
        seen_keys = set()
        obs_time = observed_at or datetime.now(timezone.utc)

        for raw_card in raw_cards:
            quote = parse_easemytrip_flight_card(
                card=raw_card,
                origin=origin,
                destination=destination,
                travel_date=travel_date,
                cabin_class=cabin_class,
                source_name=self.source_name,
                observed_at=obs_time,
            )
            if quote is not None:
                # Intra-run deduplication on physical flight identity and total fare
                dedup_key = (quote.flight_number, quote.departure_time, quote.total_fare, quote.fare_family)
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    quotes.append(quote)

        logger.info("Extracted %d unique FlightQuotes from EaseMyTrip results", len(quotes))
        return quotes

    def collect(
        self,
        origin: str = "DEL",
        destination: str = "BOM",
        travel_date: Optional[date] = None,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        cabin_class: str = "ECONOMY",
        max_scrolls: int = 2,
    ) -> List[FlightQuote]:
        """Execute automated flight search and extraction on EaseMyTrip.

        Args:
            origin: 3-letter IATA code (default: 'DEL').
            destination: 3-letter IATA code (default: 'BOM').
            travel_date: Target departure date (defaults to today + 7 days).
            adults: Number of adult passengers (default: 1).
            children: Number of child passengers (default: 0).
            infants: Number of infant passengers (default: 0).
            cabin_class: Cabin class (default: 'ECONOMY').
            max_scrolls: Number of page scrolls to trigger lazy-loaded cards.

        Returns:
            List of validated FlightQuote instances.

        Raises:
            CollectorDateMismatchError: If rendered page date disagrees with target date.
            CollectorRouteMismatchError: If rendered page route disagrees with target route.
        """
        if travel_date is None:
            travel_date = datetime.now(timezone.utc).date() + timedelta(days=7)

        search_url = self.build_search_url(
            origin=origin,
            destination=destination,
            travel_date=travel_date,
            adults=adults,
            children=children,
            infants=infants,
            cabin_class=cabin_class,
        )

        logger.info(
            "Initiating EaseMyTrip collection: %s -> %s on %s (Adults=%d, Cabin=%s)",
            origin, destination, travel_date, adults, cabin_class
        )
        self.start()
        assert self.page is not None

        try:
            logger.info("Navigating to search URL: %s", search_url)
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=self.timeout_ms)

            # Wait for flight cards to render
            logger.info("Waiting for .fltResult cards to populate in DOM...")
            self.page.wait_for_function(
                "() => document.querySelectorAll('.fltResult').length > 0 || "
                "document.body.innerText.includes('No Flights Found') || "
                "document.body.innerText.includes('Sorry, no flights')",
                timeout=self.timeout_ms,
            )

            # Settle period
            self.page.wait_for_timeout(2000)

            # Extract body text for strict Date and Route validation
            page_text = self.page.locator("body").inner_text()

            # 1. Strict Date Verification
            is_date_valid, date_msg = verify_travel_date_rendered(page_text, travel_date)
            if not is_date_valid:
                logger.error("Date validation failed: %s", date_msg)
                raise CollectorDateMismatchError(f"EaseMyTrip travel date validation failed: {date_msg}")

            # 2. Strict Route Verification
            is_route_valid, route_msg = verify_route_rendered(page_text, origin, destination)
            if not is_route_valid:
                logger.error("Route validation failed: %s", route_msg)
                raise CollectorRouteMismatchError(f"EaseMyTrip route validation failed: {route_msg}")

            # Optional scroll to load lazy-rendered cards
            for _ in range(max_scrolls):
                self.page.evaluate("window.scrollBy(0, 1500)")
                self.page.wait_for_timeout(1000)

            try:
                self._last_page_text = self.page.locator("body").inner_text()
            except Exception:
                self._last_page_text = ""

            quotes = self.extract_quotes(
                origin=origin,
                destination=destination,
                travel_date=travel_date,
                cabin_class=cabin_class,
            )

            if not quotes:
                logger.warning(
                    "No valid flight quotes extracted for %s -> %s on %s",
                    origin, destination, travel_date
                )

            return quotes

        except PlaywrightError as exc:
            logger.error("Playwright error during EaseMyTrip collection: %s", exc)
            raise
        except (CollectorDateMismatchError, CollectorRouteMismatchError):
            raise
        except Exception as exc:
            logger.error("Unexpected error during EaseMyTrip collection: %s", exc)
            raise
        finally:
            self.close()
