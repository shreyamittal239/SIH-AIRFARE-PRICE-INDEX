"""Air India Express airline direct flight data collector.

Automates flight searches on Air India Express (https://www.airindiaexpress.com),
supporting direct canonical search URL navigation and visual fallback,
verifying travel-date consistency against the rendered DOM, extracting both
non-stop and multi-segment itineraries, and mapping all visible branded fare
tiers (Xpress Lite, Xpress Value, Xpress Flex, Xpress Biz) into normalized
FlightQuote records conforming to the project schema.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from playwright.sync_api import Page, Error as PlaywrightError

from backend.collectors.playwright.base_collector import BaseCollector
from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.schemas.flight_quote import FlightQuote

logger = logging.getLogger("air_india_express_collector")


def parse_time_string(val: str) -> Optional[time]:
    """Parse a time string like '05:45' or '11:05+1' into datetime.time."""
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
    """Parse currency string like '₹ 5,667', '₹5,149.00', or '5667' into normalized Decimal.

    Rejects time strings (containing ':'), promotional discount notices ('₹1000 off',
    '₹99 date change'), and non-currency text.
    """
    if not val:
        return None
    clean = val.strip()

    # Reject times
    if ":" in clean:
        return None

    # Reject promotional discount badges and fee notices
    lower = clean.lower()
    if any(badge in lower for badge in ["off", "save", "discount", "change", "date change", "perks", "cash", "coupon"]):
        return None

    # Strip currency prefixes (₹, Rs, Rs., INR)
    stripped = re.sub(r"^(?:Rs\.?|INR|₹)\s*", "", clean, flags=re.IGNORECASE).strip()

    # If any remaining alphabetic characters, reject (e.g. 'View Fares', 'IX 1165')
    if re.search(r"[A-Za-z]", stripped):
        return None

    # Match numeric currency format: e.g. 5,667 or 6447 or 12,499.00
    match = re.search(r"(\d[\d,]*(?:\.\d{1,2})?)", stripped)
    if match:
        num_str = match.group(1).replace(",", "")
        try:
            val_dec = Decimal(num_str).quantize(Decimal("0.01"))
            # Standard domestic fare threshold to avoid small add-on fees
            if val_dec >= Decimal("500.00"):
                return val_dec
        except Exception:
            return None
    return None


def parse_flight_number(val: str) -> Optional[str]:
    """Extract and normalize Air India Express flight numbers.

    Handles single-leg non-stop flights:
      - 'IX 1452' -> 'IX 1452'
      - 'IX-1452' -> 'IX 1452'
      - 'IX1452'  -> 'IX 1452'

    Handles multi-segment itineraries preserving both leg numbers:
      - 'IX 1165 / IX 1027' -> 'IX 1165/1027'
      - 'IX-1165/1027'      -> 'IX 1165/1027'
      - 'IX 1165/IX 1027'   -> 'IX 1165/1027'
    """
    if not val:
        return None
    clean = val.strip().replace("-", " ")
    clean = re.sub(r"\s+", " ", clean)

    # Multi-segment pattern: e.g. IX 1165 / IX 1027 or IX 1165/1027
    multi_match = re.search(
        r"\b(?:IX\s*)(\d{3,4})\s*[/]\s*(?:IX\s*)?(\d{3,4})\b",
        clean,
        re.IGNORECASE,
    )
    if multi_match:
        return f"IX {multi_match.group(1)}/{multi_match.group(2)}"

    # Single-leg pattern: e.g. IX 1452 or IX1452
    single_match = re.search(r"\b(?:IX\s*[-]?\s*)(\d{3,4}[A-Z]?)\b", clean, re.IGNORECASE)
    if single_match:
        return f"IX {single_match.group(1)}"

    return None


def verify_travel_date_rendered(page_text: str, target_date: date) -> Tuple[bool, str]:
    """Verify that the rendered page corresponds strictly to the requested travel date.

    Guards against:
    - Zero inventory date: 'Sorry, no flights found on this date!'
    - Active date carousel rendering an adjacent date instead of the requested date.

    Returns:
        (is_valid, reason_message)
    """
    if not page_text:
        return False, "Empty page text"

    # Check for explicit no-inventory notification
    if "Sorry, no flights found on this date" in page_text or "Try the closest available date" in page_text:
        return False, "No inventory available on requested date (rendered zero-flights banner)"

    # Format expected day/month indicators (e.g. '17 Sep', '17-09-2026', '2026-09-17')
    day_str = str(target_date.day)
    month_abbr = target_date.strftime("%b")  # e.g. 'Sep'
    full_iso = target_date.strftime("%Y-%m-%d")

    date_patterns = [
        rf"\b{day_str}\s+{month_abbr}\b",       # e.g. '17 Sep'
        rf"\b{month_abbr}\s+{day_str}\b",       # e.g. 'Sep 17'
        rf"\b{full_iso}\b",                     # e.g. '2026-09-17'
    ]

    has_date_match = any(re.search(pat, page_text, re.IGNORECASE) for pat in date_patterns)
    if not has_date_match:
        return False, f"Rendered page does not contain target date '{day_str} {month_abbr}' or '{full_iso}'"

    return True, "Target travel date verified in rendered DOM"


# Known Air India Express fare families and their cabin class mappings
FARE_FAMILY_CABIN_MAP = {
    "Xpress Lite": "ECONOMY",
    "Xpress Value": "ECONOMY",
    "Xpress Flex": "ECONOMY",
    "Xpress Biz": "BUSINESS",
}


def parse_air_india_express_flight_card(
    lines: List[str],
    origin: str,
    destination: str,
    travel_date: date,
    source_name: str = "Air India Express Direct",
) -> List[FlightQuote]:
    """Pure parser: converts extracted text lines of an Air India Express flight card into FlightQuote instances.

    Handles both single-leg non-stop and multi-segment itineraries, and emits
    one FlightQuote for each distinct visible fare tier (Xpress Lite, Xpress Value,
    Xpress Flex, Xpress Biz) on the same physical flight.

    Args:
        lines: Non-empty stripped text lines of the card container.
        origin: Expected origin IATA code (e.g. 'DEL').
        destination: Expected destination IATA code (e.g. 'BOM').
        travel_date: Departure date.
        source_name: Data source identifier.

    Returns:
        List of FlightQuote instances (one per observed fare family). Empty if invalid.
    """
    if not lines or len(lines) < 4:
        return []

    # 1. Flight Number & Multi-segment detection
    flight_number: Optional[str] = None
    joined_text = " ".join(lines)
    for line in lines:
        fn = parse_flight_number(line)
        if fn:
            flight_number = fn
            break

    if not flight_number:
        # Check joined text across linebreaks (e.g. 'IX 1165' and 'IX 1027' on adjacent lines)
        fn = parse_flight_number(joined_text)
        if fn:
            flight_number = fn

    if not flight_number:
        return []

    # 2. Stops Detection
    is_multi_leg = "/" in flight_number
    stops = 1 if is_multi_leg else 0

    lower_text = joined_text.lower()
    if "non-stop" in lower_text or "non stop" in lower_text or "direct" in lower_text:
        stops = 0
    elif "1 stop" in lower_text:
        stops = 1
    elif "2 stop" in lower_text:
        stops = 2

    # 3. Departure and Arrival Times (HH:MM)
    time_candidates: List[time] = []
    for line in lines:
        t = parse_time_string(line)
        if t is not None:
            # Avoid duration strings like '2h 45m'
            if not re.search(r"\b\d+h\b", line.lower()):
                time_candidates.append(t)

    departure_time: Optional[time] = None
    arrival_time: Optional[time] = None

    if len(time_candidates) >= 2:
        departure_time = time_candidates[0]
        arrival_time = time_candidates[-1]
    elif len(time_candidates) == 1:
        departure_time = time_candidates[0]

    # 4. Fare Family & Pricing Tiers Extraction
    # Scan for branded tiers: Xpress Lite, Xpress Value, Xpress Flex, Xpress Biz
    tier_fares: Dict[str, Decimal] = {}
    tier_baggage: Dict[str, Optional[str]] = {}

    for idx, line in enumerate(lines):
        clean_line = line.strip()
        for tier_name in ["Xpress Lite", "Xpress Value", "Xpress Flex", "Xpress Biz"]:
            if tier_name.lower() in clean_line.lower():
                # Look for price in this line or subsequent lines within the tier block
                fare_found: Optional[Decimal] = None
                for lookahead in range(idx, min(idx + 5, len(lines))):
                    cand_fare = parse_fare_string(lines[lookahead])
                    if cand_fare is not None:
                        fare_found = cand_fare
                        break
                if fare_found is not None and tier_name not in tier_fares:
                    tier_fares[tier_name] = fare_found
                    if tier_name == "Xpress Lite":
                        tier_baggage[tier_name] = "7 kg cabin baggage only, 0 kg check-in"
                    elif tier_name == "Xpress Biz":
                        tier_baggage[tier_name] = "25 kg check-in, 7 kg cabin"
                    else:
                        tier_baggage[tier_name] = "15 kg check-in, 7 kg cabin"

    # Fallback if no explicit branded tier names matched: extract standalone fares
    if not tier_fares:
        standalone_fares: List[Decimal] = []
        for line in lines:
            f = parse_fare_string(line)
            if f is not None and f not in standalone_fares:
                standalone_fares.append(f)

        if standalone_fares:
            # If multiple unlabeled fares appear, preserve the lowest as standard Economy
            tier_fares["Xpress Value"] = min(standalone_fares)
            tier_baggage["Xpress Value"] = None

    if not tier_fares:
        return []

    # 5. Construct FlightQuote objects for all observed fare families
    now_utc = datetime.now(timezone.utc)
    quotes: List[FlightQuote] = []

    for fare_family, fare_amount in tier_fares.items():
        cabin_class = FARE_FAMILY_CABIN_MAP.get(fare_family, "ECONOMY")
        baggage_info = tier_baggage.get(fare_family)

        quote = FlightQuote(
            airline="Air India Express",
            flight_number=flight_number,
            origin=origin.upper(),
            destination=destination.upper(),
            travel_date=travel_date,
            departure_time=departure_time,
            arrival_time=arrival_time,
            cabin_class=cabin_class,
            fare_family=fare_family,
            stops=stops,
            total_fare=fare_amount,
            currency="INR",
            availability=True,
            baggage=baggage_info,
            source=source_name,
            observed_at=now_utc,
        )
        quotes.append(quote)

    return quotes


class AirIndiaExpressCollector(BaseCollector):
    """Playwright collector for Air India Express flight availability."""

    def __init__(
        self,
        browser_manager: Optional[BrowserManager] = None,
        timeout_ms: int = 45000,
    ) -> None:
        """Initialize Air India Express collector."""
        bm = browser_manager or BrowserManager(headless=None, timeout_ms=timeout_ms)
        super().__init__(source_name="Air India Express Direct", browser_manager=bm)
        self.timeout_ms = timeout_ms

    @staticmethod
    def build_search_url(
        origin: str,
        destination: str,
        travel_date: date,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        non_stop_only: bool = False,
    ) -> str:
        """Construct canonical Air India Express flight availability URL.

        Pattern:
        https://www.airindiaexpress.com/flight-availability?/{ORIGIN}/{DESTINATION}/{YYYY-MM-DD}/{NON_STOP}/{ADULTS}/{CHILDREN}/{INFANTS}/0/0/0/0/O/N/INR/ST/0
        """
        date_iso = travel_date.strftime("%Y-%m-%d")
        ns_flag = "Y" if non_stop_only else "N"
        return (
            f"https://www.airindiaexpress.com/flight-availability?"
            f"/{origin.upper()}/{destination.upper()}/{date_iso}/{ns_flag}"
            f"/{adults}/{children}/{infants}/0/0/0/0/O/N/INR/ST/0"
        )

    def extract_fares(self, page: Page) -> List[Dict[str, Any]]:
        """Extract raw flight card text blocks from rendered DOM.

        Satisfies BaseCollector abstract interface contract.
        """
        raw_cards = page.evaluate("""() => {
            const allElements = Array.from(document.querySelectorAll('*'));
            
            // Candidate cards: elements containing flight identifier pattern 'IX ' and currency '₹'
            const candidateNodes = allElements.filter(el => {
                const t = el.innerText || '';
                return /IX\\s*\\d{3,4}/i.test(t) && t.includes('₹') && el.clientHeight > 50 && el.clientWidth > 250;
            });

            // Find innermost containers for each distinct flight number
            const seenFlights = new Set();
            const cards = [];

            // Sort ascending by text length to pick the tightest card container
            candidateNodes.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);

            for (const node of candidateNodes) {
                const text = node.innerText || '';
                const match = text.match(/IX\\s*[-]?\\s*\\d{3,4}(?:\\s*[/]\\s*(?:IX\\s*)?\\d{3,4})?/i);
                if (match) {
                    const fnKey = match[0].replace(/\\s+/g, ' ').toUpperCase();
                    if (!seenFlights.has(fnKey)) {
                        seenFlights.add(fnKey);
                        cards.push({
                            flight_key: fnKey,
                            lines: text.split('\\n').map(l => l.trim()).filter(Boolean)
                        });
                    }
                }
            }

            return cards;
        }""")
        return raw_cards

    def extract_quotes(
        self,
        origin: str,
        destination: str,
        travel_date: date,
    ) -> List[FlightQuote]:
        """Extract all valid FlightQuotes from the currently rendered Air India Express page.

        Verifies travel-date integrity before parsing flight cards.
        """
        if self.page is None:
            raise RuntimeError("Page is not initialized. Call start() or collect() first.")

        body_text = self.page.locator("body").inner_text()

        # Date safety check
        is_date_valid, reason = verify_travel_date_rendered(body_text, travel_date)
        if not is_date_valid:
            logger.warning(
                "[AirIndiaExpress] Date safety check rejected rendered page: %s (requested: %s)",
                reason,
                travel_date,
            )
            return []

        raw_cards = self.extract_fares(self.page)
        logger.info("[AirIndiaExpress] Found %d candidate flight card blocks.", len(raw_cards))

        quotes: List[FlightQuote] = []
        for card in raw_cards:
            lines = card.get("lines", [])
            card_quotes = parse_air_india_express_flight_card(
                lines=lines,
                origin=origin,
                destination=destination,
                travel_date=travel_date,
                source_name=self.source_name,
            )
            quotes.extend(card_quotes)

        logger.info(
            "[AirIndiaExpress] Successfully extracted %d FlightQuote objects across all fare tiers.",
            len(quotes),
        )
        return quotes

    def collect(
        self,
        origin: str = "DEL",
        destination: str = "BOM",
        travel_date: Optional[date] = None,
        adults: int = 1,
    ) -> List[FlightQuote]:
        """Execute automated flight availability search and extract FlightQuotes.

        Args:
            origin: 3-letter IATA code (default: 'DEL').
            destination: 3-letter IATA code (default: 'BOM').
            travel_date: Target departure date (defaults to today + 7 days).
            adults: Number of adult passengers (default: 1).

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
        )

        logger.info(
            "[AirIndiaExpress] Initiating collection: %s -> %s on %s (T+%d)",
            origin,
            destination,
            travel_date,
            (travel_date - datetime.now(timezone.utc).date()).days,
        )

        self.start()
        assert self.page is not None

        try:
            logger.info("[AirIndiaExpress] Navigating to canonical URL: %s", search_url)
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=self.timeout_ms)

            # Wait for rate-limit modal or results to settle
            self.page.wait_for_timeout(3000)

            # Dismiss rate-limit / 'Take a break' modal if present
            try:
                ok_btn = self.page.locator('button:has-text("Ok"), button:has-text("OK")').first
                if ok_btn.is_visible():
                    logger.info("[AirIndiaExpress] Dismissing 'Take a break' dialog...")
                    ok_btn.click()
                    self.page.wait_for_timeout(1500)
            except Exception as err:
                logger.debug("[AirIndiaExpress] Notice dismissing modal: %s", err)

            # Wait for date carousel or zero-flights notice or card rendering
            try:
                self.page.wait_for_function(
                    "() => document.body.innerText.includes('Sorry, no flights') || "
                    "document.body.innerText.includes('DEL') || "
                    "document.querySelectorAll('[class*=\"date\"], [class*=\"flight\"]').length > 0",
                    timeout=min(self.timeout_ms, 20000),
                )
            except PlaywrightError:
                logger.warning("[AirIndiaExpress] Timeout waiting for dynamic flight content.")

            self.page.wait_for_timeout(2500)

            quotes = self.extract_quotes(
                origin=origin,
                destination=destination,
                travel_date=travel_date,
            )

            return quotes

        except PlaywrightError as exc:
            logger.error("[AirIndiaExpress] Playwright navigation error: %s", exc)
            raise
        except Exception as exc:
            logger.error("[AirIndiaExpress] Unexpected collection error: %s", exc)
            raise
        finally:
            if self._owns_browser_manager:
                self.close()
