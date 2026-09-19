from datetime import date
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.collectors.first_airline_collector import (
    FirstAirlineCollector,
    parse_flight_card_lines,
)

logging.basicConfig(level=logging.INFO)

bm = BrowserManager(headless=False, timeout_ms=35000)
bm.launch()

try:
    collector = FirstAirlineCollector(
        origin="DEL",
        destination="HYD",
        travel_date=date(2026, 10, 31),
        browser_manager=bm,
    )
    context = bm.new_context()
    page = bm.new_page(context)
    collector.page = page

    direct_url = collector.build_search_url()
    print("Direct URL:", direct_url)
    page.goto(direct_url, wait_until="domcontentloaded", timeout=35000)
    page.wait_for_timeout(5000)

    # Test the proposed updated evaluate logic
    raw_cards = page.evaluate('''() => {
        const allDivs = Array.from(document.querySelectorAll('div'));
        const flightNumDivs = allDivs.filter(d => {
            const t = d.innerText ? d.innerText.trim() : '';
            return /^SG[\s-]*\d{3,4}([\s,]+SG[\s-]*\d{3,4})*$/i.test(t);
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

    print(f"Found {len(raw_cards)} raw cards!")
    for item in raw_cards:
        print("Flight number raw:", item["flight_number"])
        lines = [l.strip() for l in item["text"].split("\n") if l.strip()]
        quote = parse_flight_card_lines(
            lines=lines,
            origin="DEL",
            destination="HYD",
            travel_date=date(2026, 10, 31),
            source_name="SpiceJet Direct",
        )
        print("Parsed Quote:", quote)

finally:
    bm.close()
