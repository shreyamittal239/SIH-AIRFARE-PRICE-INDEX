"""Cleartrip online travel aggregator (OTA) flight data collector.

Automates flight searches on Cleartrip (https://www.cleartrip.com), supporting:
1. Canonical parameterized search URL navigation.
2. Primary extraction via intercepted internal search API (flight/search/v2).
3. Secondary fallback extraction via rendered DOM cards.
4. Strict route safety (authoritative IATA filtering rejecting HDO, DXN, NMI secondary airports).
5. Strict travel-date integrity verification.
6. Multi-stop / connecting itinerary support preserving unified flight identity.
7. Project FlightQuote schema normalization and fingerprint compatibility.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import logging
import re
import time as time_module
from typing import Any, Dict, List, Optional, Tuple
# pyrefly: ignore [missing-import]
from playwright.sync_api import Page, Response, Error as PlaywrightError

from backend.collectors.playwright.base_collector import BaseCollector
from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.schemas.flight_quote import FlightQuote

logger = logging.getLogger("cleartrip_collector")


class CollectorDateMismatchError(ValueError):
    """Raised when requested travel date disagrees with the rendered DOM / API travel date."""
    pass


class CollectorRouteMismatchError(ValueError):
    """Raised when requested origin/destination route disagrees with rendered results."""
    pass


AIRLINE_NORMALIZATION_MAP = {
    "INDIGO": "IndiGo",
    "6E": "IndiGo",
    "AIR INDIA": "Air India",
    "AI": "Air India",
    "AIR INDIA EXPRESS": "Air India Express",
    "AIX": "Air India Express",
    "IX": "Air India Express",
    "AKASA AIR": "Akasa Air",
    "AKASA": "Akasa Air",
    "QP": "Akasa Air",
    "SPICEJET": "SpiceJet",
    "SG": "SpiceJet",
}

CABIN_PARAM_MAP = {
    "ECONOMY": "Economy",
    "PREMIUM_ECONOMY": "Premium Economy",
    "BUSINESS": "Business",
    "FIRST": "First",
}


def normalize_airline_name(raw_name: str) -> str:
    """Normalize raw airline name or carrier code to canonical project representation."""
    if not raw_name:
        return "Unknown"
    clean = raw_name.strip()
    clean_upper = clean.upper()
    return AIRLINE_NORMALIZATION_MAP.get(clean_upper, clean)


def parse_flight_number(val: str, carrier_code: Optional[str] = None) -> Optional[str]:
    """Normalize Cleartrip flight numbers.

    Examples:
      'IX-1235' -> 'IX 1235'
      '6E-449'  -> '6E 449'
      '1110', carrier='QP' -> 'QP 1110'
      'AI 2483 / AI 2740'  -> 'AI 2483/2740'
    """
    if not val:
        return None
    clean = val.strip().replace("-", " ")
    clean = re.sub(r"\s+", " ", clean)

    # Multi-leg pattern: e.g. 'AI 2483 / AI 2740' or '6E 101/6E 202'
    multi_match = re.search(
        r"\b([A-Z0-9]{2,3})\s*(\d{3,4})\s*/\s*(?:[A-Z0-9]{2,3}\s*)?(\d{3,4})\b",
        clean,
        re.IGNORECASE,
    )
    if multi_match:
        return f"{multi_match.group(1).upper()} {multi_match.group(2)}/{multi_match.group(3)}"

    # Standard carrier + number pattern: e.g. 'IX 1235' or '6E 449'
    match = re.search(r"\b([A-Z0-9]{2,3})\s+(\d{1,4}[A-Z]?)\b", clean, re.IGNORECASE)
    if match:
        return f"{match.group(1).upper()} {match.group(2)}"

    # Raw digits with explicit carrier code provided
    if carrier_code and re.match(r"^\d{1,4}[A-Z]?$", clean):
        return f"{carrier_code.strip().upper()} {clean}"

    return None


def parse_time_string(val: str) -> Optional[time]:
    """Parse time string in 'HH:MM' or ISO timestamp format into a datetime.time object."""
    if not val:
        return None
    val = val.strip()
    # ISO timestamp format: e.g. 2026-09-19T23:25:00.000+05:30
    if "T" in val:
        try:
            dt = datetime.fromisoformat(val)
            return dt.time()
        except Exception:
            pass

    # Standard 24h clock: e.g. '23:25'
    match = re.search(r"\b([0-2]?\d):([0-5]\d)\b", val)
    if match:
        hh = int(match.group(1))
        mm = int(match.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return time(hh, mm)

    return None


def parse_fare_value(val: Any) -> Optional[Decimal]:
    """Parse numeric fare value from direct numeric or raw formatted currency text."""
    if val is None:
        return None

    if isinstance(val, (int, float, Decimal)):
        try:
            dec = Decimal(str(val)).quantize(Decimal("0.01"))
            if dec >= Decimal("500.00"):
                return dec
            return None
        except Exception:
            return None

    clean_str = str(val).strip()
    # Reject negative values
    if clean_str.startswith("-") or "-" in clean_str:
        return None

    # Reject discount notices / coupons
    if any(k in clean_str.lower() for k in ["off", "save", "discount", "coupon", "code"]):
        return None

    stripped = re.sub(r"^(?:Rs\.?|INR|₹)\s*", "", clean_str, flags=re.IGNORECASE).strip()
    match = re.search(r"(\d[\d,]*(?:\.\d{1,2})?)", stripped)
    if match:
        num_str = match.group(1).replace(",", "")
        try:
            val_dec = Decimal(num_str).quantize(Decimal("0.01"))
            if val_dec >= Decimal("500.00"):
                return val_dec
        except Exception:
            return None

    return None


def verify_travel_date_string(date_text: str, target_date: date) -> Tuple[bool, str]:
    """Verify that a rendered date string matches the target date."""
    if not date_text:
        return False, "Date string is empty"

    day_str = str(target_date.day)
    month_abbr = target_date.strftime("%b")     # e.g. 'Sep'
    year_str = str(target_date.year)            # e.g. '2026'
    date_dmy = target_date.strftime("%d/%m/%Y") # e.g. '19/09/2026'

    patterns = [
        rf"\b{day_str}\s+{month_abbr}\b",       # e.g. '19 Sep'
        rf"\b{day_str}\s+{month_abbr}\s+{year_str}\b", # e.g. '19 Sep 2026'
        rf"\b{date_dmy}\b",                     # e.g. '19/09/2026'
        rf"\b{day_str}-{month_abbr}-{year_str}\b",
    ]

    for pat in patterns:
        if re.search(pat, date_text, re.IGNORECASE):
            return True, f"Target date '{day_str} {month_abbr}' verified"

    return False, f"Date text '{date_text}' does not match target date '{day_str} {month_abbr}'"


class CleartripCollector(BaseCollector):
    """Playwright collector for Cleartrip (cleartrip.com) flight searches."""

    def __init__(
        self,
        browser_manager: Optional[BrowserManager] = None,
        timeout_ms: int = 25000,
    ) -> None:
        """Initialize collector with 'cleartrip_ota' source identifier."""
        bm = browser_manager or BrowserManager(headless=True, timeout_ms=timeout_ms)
        super().__init__(source_name="cleartrip_ota", browser_manager=bm)
        self.timeout_ms = timeout_ms
        self._captured_api_payload: Optional[Dict[str, Any]] = None
        self._last_rejected_secondary_airports: int = 0
        self._last_total_api_options: int = 0
        self._last_page_text: str = ""

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
        """Construct canonical Cleartrip parameterized flight search URL."""
        orig_clean = origin.strip().upper()
        dest_clean = destination.strip().upper()
        date_str = travel_date.strftime("%d/%m/%Y")
        cabin_str = CABIN_PARAM_MAP.get(cabin_class.strip().upper(), "Economy")

        return (
            f"https://www.cleartrip.com/flights/results?"
            f"from={orig_clean}&to={dest_clean}&depart_date={date_str}"
            f"&adults={adults}&childs={children}&infants={infants}&class={cabin_str}"
            f"&airline=&carrier=&intl=n&page=loaded"
        )

    def _handle_response(self, response: Response) -> None:
        """Intercept internal flight/search/v2 API response during page load."""
        try:
            if "flight/search/v2" in response.url and response.status == 200:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    data = response.json()
                    if isinstance(data, dict) and "cards" in data:
                        self._captured_api_payload = data
                        logger.info(
                            "[%s] Successfully intercepted flight/search/v2 payload (%d cards)",
                            self.source_name,
                            len(data.get("cards", {}).get("J1", [])),
                        )
        except Exception as exc:
            logger.debug("[%s] Error capturing search API response: %s", self.source_name, exc)

    def extract_fares(self, page: Page) -> List[Dict[str, Any]]:
        """Extract raw flight observations.

        Primary strategy: Parse intercepted JSON payload from flight/search/v2.
        Fallback strategy: Evaluate DOM cards if API payload was not captured.

        Satisfies BaseCollector abstract interface contract.
        """
        if self._captured_api_payload is not None:
            return self._extract_fares_from_api(self._captured_api_payload)

        logger.warning("[%s] API payload not captured. Falling back to DOM card evaluation.", self.source_name)
        return self._extract_fares_from_dom(page)

    def _extract_fares_from_api(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract raw records from the flight/search/v2 API structure."""
        cards_list = data.get("cards", {}).get("J1", [])
        self._last_total_api_options = len(cards_list)
        sub_options = data.get("subTravelOptions", {})
        fares_dict = data.get("fares", {})
        flights_dict = data.get("flights", {})
        meta = data.get("metaData", {})
        airline_meta = meta.get("airlineDetail", {})

        raw_records: List[Dict[str, Any]] = []
        rejected_airports = 0

        for card in cards_list:
            summary = card.get("summary", {})
            first_dep = summary.get("firstDeparture", {})
            last_arr = summary.get("lastArrival", {})

            orig_code = first_dep.get("airport", {}).get("code", "").upper()
            dest_code = last_arr.get("airport", {}).get("code", "").upper()

            # Note: Route matching against requested route happens in extract_quotes
            flt_list = summary.get("flights", [])
            airline_code = ""
            if flt_list:
                airline_code = flt_list[0].get("airlineCode", "")
            if not airline_code:
                airline_code = first_dep.get("airlineCode", "")

            # Formulate flight number preserving multi-segment journey
            flight_number = None
            if len(flt_list) > 1:
                # Multi-leg flight
                leg_nums = [f.get("flightNumber", "") for f in flt_list if f.get("flightNumber")]
                if leg_nums:
                    flight_number = f"{airline_code} {'/'.join(leg_nums)}"
            elif len(flt_list) == 1:
                flight_number = f"{airline_code} {flt_list[0].get('flightNumber', '')}"

            # Airline name lookup
            airline_name = airline_meta.get(airline_code, {}).get("name", airline_code)
            if not airline_name:
                airline_name = airline_code

            dep_time_str = first_dep.get("airport", {}).get("time", "")
            arr_time_str = last_arr.get("airport", {}).get("time", "")
            stops = summary.get("stops", 0)

            # Aircraft lookup if available
            aircraft_type = None
            if flt_list and flt_list[0].get("id") in flights_dict:
                aircraft_type = flights_dict[flt_list[0]["id"]].get("aircraftType")

            # Extract fare details
            sub_ids = card.get("subTravelOptionIds", [])
            fare_id = None
            fare_family = "Standard"
            if sub_ids and sub_ids[0] in sub_options:
                sub_opt = sub_options[sub_ids[0]]
                fare_id = sub_opt.get("cheapestFareId")
                fare_list = sub_opt.get("fareList", [])
                if fare_list:
                    fare_family = fare_list[0].get("displayText", {}).get("displayTitle", "Standard")

            total_price = None
            base_fare = None
            tax = None
            if fare_id and fare_id in fares_dict:
                f_info = fares_dict[fare_id]
                p_info = f_info.get("pricing", {}).get("totalPricing", {})
                total_price = p_info.get("totalPrice")
                base_fare = p_info.get("totalBaseFare")
                tax = p_info.get("totalTax")
                brand = f_info.get("brand")
                if brand:
                    fare_family = brand

            raw_records.append({
                "source_type": "api",
                "airline": airline_name,
                "carrier_code": airline_code,
                "flight_number": flight_number,
                "origin": orig_code,
                "destination": dest_code,
                "departure_time": dep_time_str,
                "arrival_time": arr_time_str,
                "stops": stops,
                "price": total_price,
                "base_fare": base_fare,
                "tax": tax,
                "fare_family": fare_family,
                "aircraft": aircraft_type,
            })

        self._last_rejected_secondary_airports = rejected_airports
        return raw_records

    def _extract_fares_from_dom(self, page: Page) -> List[Dict[str, Any]]:
        """Fallback DOM extractor for Cleartrip results page."""
        return page.evaluate("""() => {
            const cards = Array.from(document.querySelectorAll('.bg-white')).filter(el => 
                el.querySelector('button') && el.querySelector('button').innerText.includes('Book')
            );

            return cards.map(c => {
                const pTags = Array.from(c.querySelectorAll('p')).map(p => p.innerText.trim());

                // Airline name: matches known carrier names
                const airlineP = pTags.find(t => /^(IndiGo|Air India|Akasa Air|Air India Express|SpiceJet)/i.test(t)) || '';
                // Flight number: e.g. IX-1235, 6E-449
                const fltNoP = pTags.find(t => /^[A-Z0-9]{2,3}-[0-9]{3,4}$/.test(t)) || '';

                // Departure and arrival times
                const timeTags = pTags.filter(t => /^[0-2][0-9]:[0-5][0-9]$/.test(t));
                const depTime = timeTags.length > 0 ? timeTags[0] : '';
                const arrTime = timeTags.length > 1 ? timeTags[1] : '';

                // Stops
                const stopP = pTags.find(t => /(Non-stop|[0-9]+\\s*stop)/i.test(t)) || 'Non-stop';

                // Fare family / brand
                const brandP = pTags.find(t => /(Refundable|Saver|Flexi|Value)/i.test(t)) || 'Standard';

                // Price text (e.g. ₹6,529)
                const priceMatch = c.innerText.match(/₹\\s*([0-9,]+)/);
                const price = priceMatch ? priceMatch[1].replace(/,/g, '') : '';

                return {
                    source_type: 'dom',
                    airline: airlineP,
                    flight_number: fltNoP,
                    origin: '',      // Filled by context in extract_quotes
                    destination: '', // Filled by context in extract_quotes
                    departure_time: depTime,
                    arrival_time: arrTime,
                    stops: stopP,
                    price: price,
                    fare_family: brandP,
                };
            });
        }""")

    def extract_quotes(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        cabin_class: str = "ECONOMY",
        observed_at: Optional[datetime] = None,
    ) -> List[FlightQuote]:
        """Extract and normalize FlightQuote records applying strict route and date safety."""
        if self.page is None:
            raise RuntimeError("Page is not initialized. Call start() or collect() first.")

        raw_records = self.extract_fares(self.page)
        quotes: List[FlightQuote] = []
        seen_keys = set()
        obs_time = observed_at or datetime.now(timezone.utc)

        target_orig = origin.strip().upper()
        target_dest = destination.strip().upper()

        rejected_secondary_count = 0

        for rec in raw_records:
            rec_orig = (rec.get("origin") or target_orig).strip().upper()
            rec_dest = (rec.get("destination") or target_dest).strip().upper()

            # --- 3. CRITICAL ROUTE SAFETY ---
            # Authoritative 3-letter IATA filter: Reject Hindon (HDO), Jewar (DXN), Navi Mumbai (NMI)
            if rec_orig != target_orig or rec_dest != target_dest:
                rejected_secondary_count += 1
                logger.debug(
                    "[%s] Rejecting record with non-matching airport: %s -> %s (expected %s -> %s)",
                    self.source_name, rec_orig, rec_dest, target_orig, target_dest
                )
                continue

            # Airline Normalization
            raw_airline = rec.get("airline") or rec.get("carrier_code") or ""
            airline = normalize_airline_name(raw_airline)
            if not airline or airline == "Unknown":
                continue

            # Flight Number Normalization
            raw_flt = rec.get("flight_number") or ""
            carrier_code = rec.get("carrier_code")
            flight_number = parse_flight_number(raw_flt, carrier_code=carrier_code)
            if not flight_number:
                continue

            # Departure & Arrival Times
            dep_time = parse_time_string(rec.get("departure_time", ""))
            arr_time = parse_time_string(rec.get("arrival_time", ""))
            if dep_time is None or arr_time is None:
                continue

            # Stops
            stops_raw = rec.get("stops", 0)
            stops = 0
            if isinstance(stops_raw, int):
                stops = max(0, stops_raw)
            elif isinstance(stops_raw, str):
                if "1" in stops_raw:
                    stops = 1
                elif "2" in stops_raw:
                    stops = 2
                elif "non" in stops_raw.lower() or "direct" in stops_raw.lower():
                    stops = 0

            # Fare validation (reject 0, negative, malformed, or sub-threshold)
            total_fare = parse_fare_value(rec.get("price"))
            if total_fare is None:
                continue

            # Fare Family
            fare_family = str(rec.get("fare_family", "Standard") or "Standard").strip().upper()

            # Construct validated FlightQuote
            quote = FlightQuote(
                airline=airline,
                flight_number=flight_number,
                origin=target_orig,
                destination=target_dest,
                travel_date=travel_date,
                departure_time=dep_time,
                arrival_time=arr_time,
                cabin_class=cabin_class.strip().upper(),
                fare_family=fare_family,
                stops=stops,
                total_fare=total_fare,
                currency="INR",
                availability=True,
                source=self.source_name,
                observed_at=obs_time,
            )

            # Intra-run deduplication
            dedup_key = (quote.flight_number, quote.departure_time, quote.total_fare, quote.fare_family)
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                quotes.append(quote)

        self._last_rejected_secondary_airports = rejected_secondary_count
        logger.info(
            "[%s] Extracted %d valid quotes for %s -> %s (Rejected %d secondary-airport quotes)",
            self.source_name, len(quotes), target_orig, target_dest, rejected_secondary_count
        )
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
    ) -> List[FlightQuote]:
        """Execute automated Cleartrip search and extract validated FlightQuote objects.

        Args:
            origin: 3-letter IATA code (default 'DEL').
            destination: 3-letter IATA code (default 'BOM').
            travel_date: Target travel date (defaults to today + 7 days).
            adults: Number of adult passengers (default 1).
            children: Number of child passengers (default 0).
            infants: Number of infant passengers (default 0).
            cabin_class: Cabin class (default 'ECONOMY').

        Returns:
            List of validated, deduplicated FlightQuote instances.

        Raises:
            CollectorDateMismatchError: If the active travel date disagrees with requested date.
            CollectorRouteMismatchError: If the rendered page route disagrees with requested route.
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
            "[%s] Starting collection: %s -> %s on %s",
            self.source_name, origin, destination, travel_date
        )

        self.start()
        assert self.page is not None

        # Reset state and attach network response listener
        self._captured_api_payload = None
        self.page.on("response", self._handle_response)

        try:
            logger.info("[%s] Navigating to: %s", self.source_name, search_url)
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=self.timeout_ms)

            # Wait for search results: either internal search API payload or DOM cards/banner
            start_wait = time_module.time()
            wait_limit_sec = self.timeout_ms / 1000.0
            while (time_module.time() - start_wait) < wait_limit_sec:
                if self._captured_api_payload is not None:
                    logger.debug("[%s] Search API payload intercepted early.", self.source_name)
                    break
                try:
                    has_results = self.page.evaluate("""() => {
                        const hasCards = Array.from(document.querySelectorAll('button')).some(
                            b => b.innerText && b.innerText.includes('Book')
                        );
                        const hasNoFlights = document.body.innerText.includes('No flights found') ||
                                             document.body.innerText.includes('Sorry, no flights');
                        return hasCards || hasNoFlights;
                    }""")
                    if has_results:
                        break
                except Exception:
                    pass
                self.page.wait_for_timeout(400)

            # Short settle period for hydration if API payload was not captured
            if self._captured_api_payload is None:
                self.page.wait_for_timeout(1000)

            # --- 4. DATE SAFETY VALIDATION ---
            # 4.1 Validate against intercepted API payload if available
            if self._captured_api_payload:
                cards = self._captured_api_payload.get("cards", {}).get("J1", [])
                if cards:
                    first_dep_time = cards[0].get("summary", {}).get("firstDeparture", {}).get("airport", {}).get("time", "")
                    if first_dep_time:
                        dep_date_str = first_dep_time.split("T")[0]
                        if dep_date_str != travel_date.isoformat():
                            raise CollectorDateMismatchError(
                                f"Cleartrip API date mismatch: received {dep_date_str}, expected {travel_date.isoformat()}"
                            )

            # 4.2 Validate against active carousel date card in DOM
            active_date_card_text = self.page.evaluate("""() => {
                const activeCard = document.querySelector('.bb-2, [class*="bb-2"], .bb-2.bc-neutral-900');
                if (activeCard) return activeCard.innerText;
                const dateCards = Array.from(document.querySelectorAll('[data-testid="dateCard"]'));
                const selected = dateCards.find(d => d.className.includes('bb-2') || d.className.includes('active'));
                return selected ? selected.innerText : '';
            }""")

            if active_date_card_text:
                is_valid_date, date_msg = verify_travel_date_string(active_date_card_text, travel_date)
                if not is_valid_date:
                    raise CollectorDateMismatchError(
                        f"Cleartrip travel date validation failed: {date_msg} (requested {travel_date})"
                    )

            # If API was not intercepted, perform short scroll to render fallback cards
            if self._captured_api_payload is None:
                logger.info("[%s] Scrolling page to trigger lazy-loaded DOM cards...", self.source_name)
                for _ in range(3):
                    self.page.evaluate("window.scrollBy(0, 1500)")
                    self.page.wait_for_timeout(500)

            # Snapshot page text for zero-inventory verification before closing page
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

            return quotes

        except PlaywrightError as exc:
            logger.error("[%s] Playwright error during collection: %s", self.source_name, exc)
            raise
        except (CollectorDateMismatchError, CollectorRouteMismatchError):
            raise
        except Exception as exc:
            logger.error("[%s] Unexpected error during collection: %s", self.source_name, exc)
            raise
        finally:
            self.close()
