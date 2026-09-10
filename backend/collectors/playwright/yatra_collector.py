"""Yatra online travel aggregator (OTA) flight data collector.

Automates flight searches on Yatra (https://www.yatra.com), handling direct
search parameterization, waiting for dynamic DOM rendering through Akamai protection,
and extracting normalized FlightQuote records conforming to the project schema.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
import re
from typing import Any, Dict, List, Optional
from playwright.sync_api import Page, Error as PlaywrightError

from backend.collectors.playwright.base_collector import BaseCollector
from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.schemas.flight_quote import FlightQuote

logger = logging.getLogger("yatra_collector")


def parse_time_string(val: str) -> Optional[time]:
    """Parse time string like '05:45' or '23:25' into datetime.time."""
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", val)
    if match:
        try:
            return time(int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    return None


def parse_fare_string(val: str) -> Optional[Decimal]:
    """Parse currency string like '₹ 6,447', 'Rs. 6,447', or '5,667' into normalized Decimal.

    Rejects time strings (containing ':') and non-currency alphabetical strings.
    """
    clean_val = val.strip()
    # Reject times (e.g. 05:45)
    if ":" in clean_val:
        return None

    # Strip currency prefixes (e.g. Rs, Rs., INR, ₹)
    stripped_prefix = re.sub(r"^(?:Rs\.?|INR|₹)\s*", "", clean_val, flags=re.IGNORECASE)

    # Reject alphanumeric flight numbers or words (e.g. SG-9091, New Delhi, 1 Stop, View Fares)
    if re.search(r"[A-Za-z]", stripped_prefix):
        return None

    # Must match numeric currency pattern: e.g. 5,667 or 6529.00
    match = re.search(r"(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)", stripped_prefix)
    if match:
        num_str = match.group(1).replace(",", "")
        try:
            val_dec = Decimal(num_str).quantize(Decimal("0.01"))
            if val_dec > Decimal("0.00"):
                return val_dec
        except Exception:
            return None
    return None


def parse_flight_number(val: str) -> Optional[str]:
    """Normalize Yatra flight numbers (e.g. 'SG-9091' -> 'SG 9091', 'IX-1165/1027' -> 'IX 1165/1027')."""
    clean = val.strip().replace("-", " ")
    clean = re.sub(r"\s+", " ", clean)
    match = re.search(r"\b([A-Z0-9]{2,3}\s+\d{3,4}(?:/\d{3,4})?)\b", clean, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def parse_yatra_flight_card(
    lines: List[str],
    origin: str,
    destination: str,
    travel_date: date,
    cabin_class: str = "ECONOMY",
    source_name: str = "Yatra",
) -> Optional[FlightQuote]:
    """Pure parser: converts extracted text lines of a Yatra .flight-det card into a FlightQuote.

    Example line sequences from Yatra DOM:
    ['Air India Express', 'IX-1165/1027', '05:45', 'New Delhi', '11:05', 'Mumbai', '5h 20m', '1 Stop', '5,667', 'View Fares']
    ['SpiceJet', 'SG-9091', '05:30', 'New Delhi', '07:50', 'Mumbai', '2h 20m', 'Non Stop', '6,447', 'View Fares']
    ['Air India Express', 'IX-1392/1027', '23:25', 'New Delhi', '11:05', '+ 1 day', 'Mumbai', '11h 40m', '1 Stop', '6,469', 'View Fares']

    Args:
        lines: Non-empty stripped text lines inside the flight card element.
        origin: Expected origin IATA code (e.g. 'DEL').
        destination: Expected destination IATA code (e.g. 'BOM').
        travel_date: Departure date.
        cabin_class: Cabin class (default 'ECONOMY').
        source_name: Data source identifier (default 'Yatra').

    Returns:
        FlightQuote instance or None if required minimum fields cannot be determined.
    """
    if not lines or len(lines) < 6:
        return None

    airline: Optional[str] = None
    flight_number: Optional[str] = None
    departure_time: Optional[time] = None
    arrival_time: Optional[time] = None
    stops: int = 0
    total_fare: Optional[Decimal] = None

    # Known airlines on Indian domestic routes
    known_airlines = [
        "IndiGo",
        "Air India Express",
        "Air India",
        "SpiceJet",
        "Akasa Air",
        "Vistara",
        "Alliance Air",
    ]

    # 1. Identify Airline
    for line in lines:
        for ka in known_airlines:
            if ka.lower() in line.lower():
                airline = ka
                break
        if airline:
            break

    if not airline and len(lines) > 0:
        # Check if first line doesn't look like a number or time
        if not re.search(r"^\d", lines[0]):
            airline = lines[0].strip()

    if not airline:
        return None

    # 2. Identify Flight Number
    for line in lines:
        fn = parse_flight_number(line)
        if fn and any(prefix in fn for prefix in ["6E", "SG", "IX", "AI", "QP", "UK", "9I"]):
            flight_number = fn
            break

    if not flight_number and len(lines) > 1:
        fn = parse_flight_number(lines[1])
        if fn:
            flight_number = fn

    if not flight_number:
        return None

    # 3. Identify Departure and Arrival Times (HH:MM)
    time_candidates: List[time] = []
    for line in lines:
        t = parse_time_string(line)
        if t is not None:
            # Skip duration lines like '5h 20m'
            if not re.search(r"\b\d+h\b", line.lower()):
                time_candidates.append(t)

    if len(time_candidates) >= 2:
        departure_time = time_candidates[0]
        arrival_time = time_candidates[1]

    # 4. Identify Stops
    for line in lines:
        lower = line.lower()
        if "non stop" in lower or "non-stop" in lower or "direct" in lower:
            stops = 0
            break
        elif "1 stop" in lower:
            stops = 1
            break
        elif "2 stop" in lower:
            stops = 2
            break

    # 5. Identify Total Fare
    for line in lines:
        # Avoid promotional discount badges or promo codes like '3,000 OFF'
        if any(bad in line.lower() for bad in ["off", "save", "ecash", "cash", "coupon", "code", "stop", "delhi", "mumbai"]):
            continue
        fare_cand = parse_fare_string(line)
        if fare_cand and fare_cand >= Decimal("1000.00"):
            total_fare = fare_cand
            break

    if total_fare is None:
        return None

    return FlightQuote(
        airline=airline,
        flight_number=flight_number,
        origin=origin.upper(),
        destination=destination.upper(),
        travel_date=travel_date,
        departure_time=departure_time,
        arrival_time=arrival_time,
        cabin_class=cabin_class.upper(),
        fare_family=None,  # Not exposed in summary cards
        stops=stops,
        total_fare=total_fare,
        currency="INR",
        availability=True,
        baggage=None,
        source=source_name,
        observed_at=datetime.now(timezone.utc),
    )


class YatraCollector(BaseCollector):
    """Playwright collector for Yatra.com flight searches."""

    def __init__(
        self,
        browser_manager: Optional[BrowserManager] = None,
        timeout_ms: int = 60000,
    ) -> None:
        """Initialize collector with 'Yatra' source identifier."""
        # Yatra's Akamai CDN resets headless HTTP/2 streams; standard headful mode is required.
        bm = browser_manager or BrowserManager(headless=False, timeout_ms=timeout_ms)
        super().__init__(source_name="Yatra", browser_manager=bm)
        self.timeout_ms = timeout_ms

    @staticmethod
    def build_search_url(
        origin: str,
        destination: str,
        travel_date: date,
        adults: int = 1,
        cabin_class: str = "ECONOMY",
    ) -> str:
        """Construct canonical Yatra air search trigger URL.

        Format:
        https://flight.yatra.com/air-search-ui/dom2/trigger?flex=0&viewName=normal&source=fresco-flights&type=O&class=Economy&ADT=1&CHD=0&INF=0&noOfSegments=1&origin=DEL&originCountry=IN&destination=BOM&destinationCountry=IN&flight_depart_date=16/09/2026&arrivalDate=
        """
        date_str = travel_date.strftime("%d/%m/%Y")
        cabin_formatted = cabin_class.title()
        return (
            f"https://flight.yatra.com/air-search-ui/dom2/trigger?"
            f"flex=0&viewName=normal&source=fresco-flights&type=O"
            f"&class={cabin_formatted}&ADT={adults}&CHD=0&INF=0&noOfSegments=1"
            f"&origin={origin.upper()}&originCountry=IN"
            f"&destination={destination.upper()}&destinationCountry=IN"
            f"&flight_depart_date={date_str}&arrivalDate="
        )

    def extract_fares(self, page: Page) -> List[Dict[str, Any]]:
        """Extract raw flight card dictionaries from the loaded Yatra results page.

        Satisfies BaseCollector abstract interface contract.
        """
        raw_cards = page.evaluate("""() => {
            const cards = Array.from(document.querySelectorAll('.flight-det'));
            return cards.map(c => {
                const rect = c.getBoundingClientRect();
                const isVis = rect.width > 0 && rect.height > 0 && window.getComputedStyle(c).display !== 'none';
                return {
                    visible: isVis,
                    lines: c.innerText.split('\\n').map(l => l.trim()).filter(Boolean)
                };
            });
        }""")
        return [c for c in raw_cards if c.get("visible", True) and c.get("lines")]

    def extract_quotes(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        cabin_class: str = "ECONOMY",
    ) -> List[FlightQuote]:
        """Extract all valid flight quotes from the currently rendered Yatra results page."""
        if self.page is None:
            raise RuntimeError("Page is not initialized. Call start() or collect() first.")

        raw_cards = self.extract_fares(self.page)

        quotes: List[FlightQuote] = []
        seen_keys = set()

        for card in raw_cards:
            lines = card.get("lines", [])
            quote = parse_yatra_flight_card(
                lines=lines,
                origin=origin,
                destination=destination,
                travel_date=travel_date,
                cabin_class=cabin_class,
                source_name=self.source_name,
            )
            if quote is not None:
                # Deduplicate identical cards on the page
                dedup_key = (quote.flight_number, quote.departure_time, quote.total_fare)
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    quotes.append(quote)

        logger.info("Extracted %d unique FlightQuotes from Yatra results", len(quotes))
        return quotes

    def collect(
        self,
        origin: str = "DEL",
        destination: str = "BOM",
        travel_date: Optional[date] = None,
        adults: int = 1,
        cabin_class: str = "ECONOMY",
        max_scrolls: int = 2,
    ) -> List[FlightQuote]:
        """Perform automated flight search and quote extraction on Yatra.

        Args:
            origin: 3-letter IATA code (default: 'DEL').
            destination: 3-letter IATA code (default: 'BOM').
            travel_date: Target departure date (defaults to today + 7 days).
            adults: Number of adult passengers (default: 1).
            cabin_class: Cabin class (default: 'ECONOMY').
            max_scrolls: Number of page scrolls to trigger dynamic loading of results.

        Returns:
            List of normalized FlightQuote objects.
        """
        if travel_date is None:
            travel_date = datetime.now(timezone.utc).date() + timedelta(days=7)

        search_url = self.build_search_url(
            origin=origin,
            destination=destination,
            travel_date=travel_date,
            adults=adults,
            cabin_class=cabin_class,
        )

        logger.info("Initiating Yatra collection: %s -> %s on %s", origin, destination, travel_date)
        self.start()
        assert self.page is not None

        try:
            logger.info("Navigating to Yatra search URL: %s", search_url)
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=self.timeout_ms)

            # Wait for Challenge Validation and results DOM to load
            logger.info("Waiting for flight results to render...")
            self.page.wait_for_function(
                "() => document.querySelectorAll('.flight-det').length > 0 || "
                "(document.title.includes('flights') && !document.title.includes('Challenge'))",
                timeout=self.timeout_ms,
            )

            # Allow initial result animations to settle
            self.page.wait_for_timeout(3000)

            # Scroll down to load additional flight cards if requested
            for scroll_idx in range(max_scrolls):
                self.page.evaluate("window.scrollBy(0, 1500)")
                self.page.wait_for_timeout(1500)

            quotes = self.extract_quotes(
                origin=origin,
                destination=destination,
                travel_date=travel_date,
                cabin_class=cabin_class,
            )

            if not quotes:
                logger.warning(
                    "No flight quotes found for %s -> %s on %s. Page title: '%s'",
                    origin,
                    destination,
                    travel_date,
                    self.page.title(),
                )

            return quotes

        except PlaywrightError as exc:
            logger.error("Playwright error during Yatra collection: %s", exc)
            raise
        except Exception as exc:
            logger.error("Unexpected error during Yatra collection: %s", exc)
            raise
        finally:
            if self._owns_browser_manager:
                self.close()
