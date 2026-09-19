"""SpiceJet airline proof-of-concept collector.

Automates navigating SpiceJet's domestic flight search portal,
handling origin/destination and date selection, submitting the search,
and extracting normalized FlightQuote records from the rendered results DOM.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional
from playwright.sync_api import Page, Error as PlaywrightError

from backend.collectors.playwright.base_collector import BaseCollector
from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.schemas.flight_quote import FlightQuote

logger = logging.getLogger("spicejet_collector")


def parse_time_string(val: str) -> Optional[time]:
    """Parse time string like '19:55' or '01:25+1' into datetime.time."""
    match = re.search(r"(\d{1,2}):(\d{2})", val)
    if match:
        try:
            return time(int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    return None


def parse_fare_string(val: str) -> Optional[Decimal]:
    """Parse price string like '₹ 7,469' or '7085' into Decimal."""
    clean = re.sub(r"[^\d.]", "", val)
    if clean:
        try:
            return Decimal(clean)
        except Exception:
            return None
    return None


def parse_flight_card_lines(
    lines: List[str],
    origin: str,
    destination: str,
    travel_date: date,
    source_name: str = "SpiceJet Direct",
) -> Optional[FlightQuote]:
    """Pure parsing function: converts text lines of a flight card into a FlightQuote.

    Args:
        lines: Stripped non-empty text lines extracted from a flight card.
        origin: Expected origin code.
        destination: Expected destination code.
        travel_date: Departure date.
        source_name: Source identifier.

    Returns:
        FlightQuote instance or None if minimum required fields cannot be found.
    """
    flight_num = None
    dep_time = None
    arr_time = None
    stops = 0
    fares: List[Decimal] = []

    # Find flight number (e.g. 'SG 162', 'SG-2802')
    for line in lines:
        match = re.search(r"\b(SG\s*[-]?\s*\d{3,4})\b", line, re.IGNORECASE)
        if match:
            flight_num = match.group(1).replace("-", " ").strip()
            break

    if not flight_num:
        return None

    # Detect times (HH:MM)
    time_matches = []
    for line in lines:
        t_match = re.search(r"\b(\d{1,2}:\d{2}(?:\+\d+)?)\b", line)
        if t_match and not any(k in line.lower() for k in ["point", "earn"]):
            time_matches.append(t_match.group(1))

    if len(time_matches) >= 2:
        dep_time = parse_time_string(time_matches[0])
        arr_time = parse_time_string(time_matches[1])

    # Detect stops
    for line in lines:
        lower = line.lower()
        if "direct" in lower or "non-stop" in lower:
            stops = 0
            break
        elif "1 stop" in lower or "1-stop" in lower:
            stops = 1
            break
        elif "2 stop" in lower or "2-stop" in lower:
            stops = 2
            break
        elif "connecting" in lower or "halt at" in lower:
            stops = 1
            break

    # Detect fares (e.g., lines with ₹ or numbers preceded/followed by Rupee)
    for line in lines:
        if "₹" in line or "rs" in line.lower():
            fare_val = parse_fare_string(line)
            if fare_val and fare_val > 500:  # Sensible minimum flight price
                fares.append(fare_val)

    if not fares:
        # Fallback: check if line is a pure price number with commas
        for line in lines:
            if re.match(r"^\d{1,2},\d{3}$", line.strip()):
                fare_val = parse_fare_string(line)
                if fare_val:
                    fares.append(fare_val)

    if not fares:
        return None

    # Lowest quoted fare on the card is typically the primary baseline fare
    lowest_fare = min(fares)

    return FlightQuote(
        airline="SpiceJet",
        flight_number=flight_num,
        origin=origin.upper(),
        destination=destination.upper(),
        travel_date=travel_date,
        departure_time=dep_time,
        arrival_time=arr_time,
        cabin_class="ECONOMY",
        fare_family="SpiceSaver",
        stops=stops,
        total_fare=lowest_fare,
        currency="INR",
        availability=True,
        baggage=None,  # Not exposed in collapsed overview card
        source=source_name,
        observed_at=datetime.now(timezone.utc),
    )


class FirstAirlineCollector(BaseCollector):
    """Playwright-based flight quote collector for SpiceJet."""

    def __init__(
        self,
        origin: Optional[str] = None,
        destination: Optional[str] = None,
        travel_date: Optional[date] = None,
        passengers: Optional[int] = None,
        cabin_class: Optional[str] = None,
        browser_manager: Optional[BrowserManager] = None,
    ) -> None:
        super().__init__(source_name="SpiceJet Direct", browser_manager=browser_manager)

        self.origin = (origin or os.getenv("ORIGIN", "DEL")).strip().upper()
        self.destination = (
            destination or os.getenv("DESTINATION", "BOM")
        ).strip().upper()

        if travel_date:
            self.travel_date = travel_date
        else:
            env_date = os.getenv("TRAVEL_DATE")
            if env_date:
                self.travel_date = date.fromisoformat(env_date)
            else:
                # Default T+7 booking window
                self.travel_date = date.today() + timedelta(days=7)

        self.passengers = passengers or int(os.getenv("PASSENGERS", "1"))
        self.cabin_class = cabin_class or os.getenv("CABIN_CLASS", "ECONOMY")
        self.is_zero_inventory = False
        self.no_inventory_reason: Optional[str] = None

    def build_search_url(self) -> str:
        """Construct the direct search URL as a robust navigation fallback."""
        date_str = self.travel_date.strftime("%Y-%m-%d")
        return (
            f"https://www.spicejet.com/search?from={self.origin}&to={self.destination}"
            f"&tripType=1&departure={date_str}&adult={self.passengers}&child=0&srCitizen=0"
            f"&infant=0&currency=INR&redirectTo=/"
        )

    def search_via_form(self, page: Page) -> bool:
        """Automate interactive form-based search on the SpiceJet homepage."""
        logger.info("[SpiceJet] Navigating to homepage: https://www.spicejet.com/")
        page.goto("https://www.spicejet.com/", wait_until="domcontentloaded", timeout=35000)
        page.wait_for_timeout(3000)

        # 1. Select Origin
        origin_input = page.locator("[data-testid='to-test-id-origin'] input")
        if origin_input.is_visible():
            origin_input.click()
            page.wait_for_timeout(500)
            origin_opt = page.locator(f"div:has-text('{self.origin}')").first
            if origin_opt.is_visible():
                origin_opt.click()

        # 2. Select Destination
        logger.info("[SpiceJet] Selecting destination: %s", self.destination)
        dest_input = page.locator("[data-testid='to-test-id-destination'] input")
        if dest_input.is_visible():
            dest_input.click()
            page.wait_for_timeout(500)
            dest_opt = page.locator(f"div[data-testid*='auto-cmp-opt']:has-text('{self.destination}')").first
            if not dest_opt.is_visible():
                dest_opt = page.locator(f"div:has-text('{self.destination}')").first
            if dest_opt.is_visible():
                dest_opt.click()
                logger.info("[SpiceJet] Clicked destination city: %s", self.destination)

        page.wait_for_timeout(1000)

        # 3. Select Travel Date in Calendar
        day_str = str(self.travel_date.day)
        logger.info("[SpiceJet] Selecting travel day in calendar: %s", day_str)
        try:
            day_cell = page.locator(f"div[data-testid*='calendar-day']:has-text('{day_str}')").first
            if day_cell.is_visible():
                day_cell.click()
            else:
                day_fallback = page.locator(f"div:has-text('{day_str}')").first
                if day_fallback.is_visible():
                    day_fallback.click()
        except Exception as err:
            logger.warning("[SpiceJet] Calendar day selection encountered notice: %s", err)

        page.wait_for_timeout(1000)

        # 4. Click Search Flights CTA
        logger.info("[SpiceJet] Submitting flight search...")
        search_btn = page.locator("[data-testid='home-page-flight-cta']")
        if search_btn.is_visible():
            search_btn.click(force=True)
            logger.info("[SpiceJet] Search CTA clicked.")
            page.wait_for_timeout(4000)
            return True
        return False

    def navigate_search(self) -> Page:
        """Execute search flow: attempts form interaction, falling back to direct URL."""
        if self.page is None:
            self.start()
            assert self.page is not None

        form_success = False
        try:
            form_success = self.search_via_form(self.page)
        except Exception as err:
            logger.warning("[SpiceJet] Form interaction encountered notice: %s", err)

        # If still on homepage or form didn't trigger search, use direct search URL
        if not form_success or "search?" not in self.page.url:
            direct_url = self.build_search_url()
            logger.info("[SpiceJet] Loading search results via direct URL: %s", direct_url)
            self.page.goto(direct_url, wait_until="domcontentloaded", timeout=35000)

        logger.info("[SpiceJet] Waiting for flight results or zero-inventory indicator...")
        try:
            # Combined wait: resolves immediately when EITHER flight cards OR explicit zero-inventory text appears
            self.page.wait_for_function(
                r"""() => {
                    const text = document.body ? document.body.innerText.toLowerCase() : '';
                    const hasZeroInv = text.includes('no flights available') ||
                                       text.includes('sorry, no flights') ||
                                       text.includes('no flights found') ||
                                       text.includes('no available flights') ||
                                       text.includes('unfortunately, there are no flights');
                    const hasCards = Array.from(document.querySelectorAll('div, span')).some(d => {
                        const t = d.innerText ? d.innerText.trim() : '';
                        return /^SG[\s-]*\d{3,4}([\s,]+SG[\s-]*\d{3,4})*$/i.test(t) || (t.startsWith('SG ') && t.length < 35);
                    });
                    return hasZeroInv || hasCards;
                }""",
                timeout=25000,
            )
            self.page.wait_for_timeout(2000)
            logger.info("[SpiceJet] Flight results DOM ready: URL=%s", self.page.url)

            # Check for explicit zero inventory in rendered DOM
            body_text = self.page.locator("body").inner_text()
            lower_text = body_text.lower()
            zero_inv_phrases = [
                "unfortunately, there are no flights available",
                "there are no flights available",
                "no flights available",
                "no flights found",
                "sorry, no flights",
                "no available flights",
            ]
            for phrase in zero_inv_phrases:
                if phrase in lower_text:
                    logger.info(
                        "[SpiceJet] Explicit zero-inventory confirmed for %s -> %s on %s: '%s'",
                        self.origin,
                        self.destination,
                        self.travel_date,
                        phrase,
                    )
                    self.is_zero_inventory = True
                    self.no_inventory_reason = f"SpiceJet confirmed: '{phrase}'"
                    break

        except PlaywrightError as err:
            logger.error("[SpiceJet] Timed out waiting for flight results or zero-inventory indicator: %s", err)
            raise

        return self.page

    def extract_fares(self, page: Page) -> List[Dict[str, Any]]:
        """Extract raw flight rows from DOM."""
        raw_cards = page.evaluate('''() => {
            const allDivs = Array.from(document.querySelectorAll('div'));
            const flightNumDivs = allDivs.filter(d => {
                const t = d.innerText ? d.innerText.trim() : '';
                return /^SG[\\s-]*\\d{3,4}([\\s,]+SG[\\s-]*\\d{3,4})*$/i.test(t);
            });
            
            const seen = new Set();
            const results = [];

            for (const fnDiv of flightNumDivs) {
                const fnText = fnDiv.innerText.trim();
                if (seen.has(fnText)) continue;
                
                let parent = fnDiv.parentElement;
                let card = null;
                for (let i = 0; i < 7; i++) {
                    if (!parent) break;
                    if (parent.innerText && parent.innerText.includes('₹')) {
                        card = parent;
                        break;
                    }
                    parent = parent.parentElement;
                }
                if (!card) card = fnDiv.parentElement.parentElement;
                
                seen.add(fnText);
                results.push({
                    flight_number: fnText,
                    text: card ? card.innerText : fnDiv.innerText
                });
            }
            return results;
        }''')
        return raw_cards

    def collect_quotes(self) -> List[FlightQuote]:
        """Orchestrate search and parse extracted rows into FlightQuote instances."""
        self.navigate_search()
        assert self.page is not None

        if getattr(self, "is_zero_inventory", False):
            logger.info(
                "[SpiceJet] Returning empty quotes list due to confirmed zero inventory: %s",
                getattr(self, "no_inventory_reason", "No flights available"),
            )
            return []

        raw_cards = self.extract_fares(self.page)
        logger.info("[SpiceJet] Raw flight candidate rows found: %d", len(raw_cards))

        quotes: List[FlightQuote] = []
        for item in raw_cards:
            lines = [l.strip() for l in item["text"].split("\n") if l.strip()]
            quote = parse_flight_card_lines(
                lines=lines,
                origin=self.origin,
                destination=self.destination,
                travel_date=self.travel_date,
                source_name=self.source_name,
            )
            if quote:
                quotes.append(quote)
                logger.info(
                    "[SpiceJet] Extracted flight: %s | %s -> %s | Departure: %s | Arrival: %s | Fare: ₹%s",
                    quote.flight_number,
                    quote.origin,
                    quote.destination,
                    quote.departure_time,
                    quote.arrival_time,
                    quote.total_fare,
                )

        logger.info("[SpiceJet] Successfully parsed %d FlightQuote objects.", len(quotes))
        return quotes


# Alias for explicit naming
SpiceJetCollector = FirstAirlineCollector


if __name__ == "__main__":
    # Ensure stdout handles UTF-8 (Rupee symbol etc) on Windows consoles
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    print("==================================================")
    print("  SpiceJet Real Airline Collector Proof-of-Concept")
    print("==================================================")

    # Use visible browser (headless=False) so developer can visually verify
    use_headless = "--headless" in sys.argv or os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
    manager = BrowserManager(headless=use_headless)

    collector = FirstAirlineCollector(browser_manager=manager)

    try:
        results = collector.collect_quotes()
        print(f"\n--- EXTRACTED FLIGHT QUOTES ({len(results)} found) ---")
        for i, q in enumerate(results, 1):
            print(f"\n[Quote #{i}]")
            print(f"  Airline      : {q.airline}")
            print(f"  Flight Number: {q.flight_number}")
            print(f"  Route        : {q.origin} -> {q.destination}")
            print(f"  Travel Date  : {q.travel_date}")
            print(f"  Departure    : {q.departure_time}")
            print(f"  Arrival      : {q.arrival_time}")
            print(f"  Stops        : {q.stops}")
            print(f"  Fare Family  : {q.fare_family}")
            print(f"  Cabin Class  : {q.cabin_class}")
            print(f"  Total Fare   : {q.currency} {q.total_fare}")
            print(f"  Source       : {q.source}")
            print(f"  Observed At  : {q.observed_at.isoformat()}")
        print("\n==================================================")
        print("Collector POC execution completed successfully.")
        sys.exit(0)
    except Exception as exc:
        print(f"\nCollector execution encountered error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        collector.close()
