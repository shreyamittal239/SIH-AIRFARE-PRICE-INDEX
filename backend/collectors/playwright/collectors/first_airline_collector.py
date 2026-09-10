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
        elif "1 stop" in lower:
            stops = 1
            break
        elif "2 stop" in lower:
            stops = 2
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

    def build_search_url(self) -> str:
        """Construct the direct search URL as a robust navigation fallback."""
        date_str = self.travel_date.strftime("%Y-%m-%d")
        return (
            f"https://www.spicejet.com/search?from={self.origin}&to={self.destination}"
            f"&tripType=1&departure={date_str}&adult={self.passengers}"
            f"&child=0&srCitizen=0&infant=0&currency=INR&redirectTo=/"
        )

    def search_via_form(self, page: Page) -> bool:
        """Navigate to homepage and submit flight search through the visual form."""
        logger.info("[SpiceJet] Navigating to homepage: https://www.spicejet.com/")
        page.goto("https://www.spicejet.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)

        # 1. Interact with Destination box
        logger.info("[SpiceJet] Selecting destination: %s", self.destination)
        dest_box = page.locator("[data-testid='to-testID-destination']")
        if not dest_box.is_visible():
            logger.warning("[SpiceJet] Destination box not visible on homepage.")
            return False
        dest_box.click()
        page.wait_for_timeout(1000)

        # 2. Select target destination city (e.g. BOM / Mumbai)
        city_locator = page.locator(f"div:has-text('{self.destination}')").last
        if not city_locator.is_visible():
            # Scroll city container
            page.mouse.wheel(0, 300)
            page.wait_for_timeout(500)
            city_locator = page.locator(f"div:has-text('{self.destination}')").last

        if city_locator.is_visible():
            city_locator.click()
            logger.info("[SpiceJet] Clicked destination city: %s", self.destination)
        else:
            logger.warning("[SpiceJet] City %s not found in list.", self.destination)
            return False

        page.wait_for_timeout(1000)

        # 3. Calendar opens automatically. Select target day to dismiss
        day_num = str(self.travel_date.day)
        logger.info("[SpiceJet] Selecting travel day in calendar: %s", day_num)
        day_locator = page.locator(f"div[data-testid*='calendar-day-{day_num}']").first
        if day_locator.is_visible():
            day_locator.click()
        else:
            # Fallback: dismiss calendar via Escape
            page.keyboard.press("Escape")

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

        logger.info("[SpiceJet] Waiting for flight results to load...")
        try:
            self.page.wait_for_selector("div:has-text('SG ')", timeout=25000)
            self.page.wait_for_timeout(3000)
            logger.info("[SpiceJet] Flight results DOM ready: URL=%s", self.page.url)
        except PlaywrightError as err:
            logger.error("[SpiceJet] Timed out waiting for flight results: %s", err)
            raise

        return self.page

    def extract_fares(self, page: Page) -> List[Dict[str, Any]]:
        """Extract raw flight rows from DOM."""
        raw_cards = page.evaluate('''() => {
            const allDivs = Array.from(document.querySelectorAll('div'));
            const flightNumDivs = allDivs.filter(d => /^SG\\s*\\d{3,4}$/.test(d.innerText ? d.innerText.trim() : ''));
            
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
