"""Diagnostic script to inspect the exact page state for the 3 failed SpiceJet tasks:
1. DEL-HYD T+45 (2026-10-31)
2. CCU-BOM T+7 (2026-09-23)
3. CCU-BOM T+15 (2026-10-01)
"""

from datetime import date
import json
import logging
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.collectors.first_airline_collector import FirstAirlineCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("spicejet_diagnostic")

TASKS = [
    {"route": "DEL-HYD", "origin": "DEL", "destination": "HYD", "window": "T+45", "travel_date": date(2026, 10, 31)},
    {"route": "CCU-BOM", "origin": "CCU", "destination": "BOM", "window": "T+7", "travel_date": date(2026, 9, 23)},
    {"route": "CCU-BOM", "origin": "CCU", "destination": "BOM", "window": "T+15", "travel_date": date(2026, 10, 1)},
]

OUT_DIR = Path("scratch/spicejet_diagnostic_output")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def diagnose():
    bm = BrowserManager(headless=False, timeout_ms=45000)
    bm.launch()
    results = []

    try:
        for idx, task in enumerate(TASKS, 1):
            logger.info("=" * 60)
            logger.info(f"DIAGNOSING [{idx}/3]: {task['route']} | {task['window']} | Date: {task['travel_date']}")
            logger.info("=" * 60)

            collector = FirstAirlineCollector(
                origin=task["origin"],
                destination=task["destination"],
                travel_date=task["travel_date"],
                browser_manager=bm,
            )

            context = bm.new_context()
            page = bm.new_page(context)
            collector.page = page

            task_res = {
                "route": task["route"],
                "window": task["window"],
                "travel_date": str(task["travel_date"]),
            }

            try:
                # 1. Try search via form
                form_ok = False
                try:
                    form_ok = collector.search_via_form(page)
                except Exception as e:
                    logger.warning("search_via_form error: %s", e)

                # 2. If not on search results, use direct URL
                if not form_ok or "search?" not in page.url:
                    direct_url = collector.build_search_url()
                    logger.info("Navigating direct search URL: %s", direct_url)
                    page.goto(direct_url, wait_until="domcontentloaded", timeout=35000)

                # Wait for initial load
                page.wait_for_timeout(6000)

                # Capture basic metadata
                task_res["final_url"] = page.url
                task_res["page_title"] = page.title()

                # Screenshot
                ss_path = OUT_DIR / f"{task['route']}_{task['window']}.png"
                try:
                    page.screenshot(path=str(ss_path), full_page=True)
                    task_res["screenshot"] = str(ss_path)
                except Exception as e:
                    task_res["screenshot_error"] = str(e)

                # Capture body innerText
                body_text = page.locator("body").inner_text()
                txt_path = OUT_DIR / f"{task['route']}_{task['window']}_text.txt"
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(body_text)
                task_res["body_text_len"] = len(body_text)

                # Scan for flight numbers or cards
                raw_cards = collector.extract_fares(page)
                task_res["extracted_raw_cards_count"] = len(raw_cards)
                task_res["extracted_raw_cards"] = raw_cards

                # Check for specific phrases or keywords
                lower_text = body_text.lower()
                matched_phrases = []
                test_phrases = [
                    "unfortunately, there are no flights available",
                    "no flights available",
                    "there are no flights available",
                    "no flights found",
                    "sorry, no flights",
                    "no available flights",
                    "no direct flights",
                    "sold out",
                    "not operating",
                    "select another date",
                    "no results",
                    "closest available",
                    "modify search",
                ]
                for p in test_phrases:
                    if p in lower_text:
                        matched_phrases.append(p)
                task_res["matched_phrases"] = matched_phrases

                # Check all div texts that might look like banners or alerts
                alert_texts = page.evaluate('''() => {
                    const elements = Array.from(document.querySelectorAll('div, span, p, h1, h2, h3, h4'));
                    return elements
                        .map(e => e.innerText ? e.innerText.trim() : '')
                        .filter(t => t.length > 5 && t.length < 200)
                        .filter(t => /no flight|sorry|unavailable|sold out|not available|modify|unfortunately|no direct/i.test(t));
                }''')
                task_res["alert_texts"] = list(set(alert_texts))

                # Check if there are calendar date cells or tabs
                date_headers = page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('div'))
                        .map(d => d.innerText ? d.innerText.trim() : '')
                        .filter(t => /₹|INR/i.test(t) && t.length < 40);
                }''')
                task_res["date_headers_sample"] = date_headers[:10]

                logger.info("Result for %s %s: URL=%s, Cards=%d, MatchedPhrases=%s, AlertTexts=%s",
                            task["route"], task["window"], page.url, len(raw_cards), matched_phrases, alert_texts)

            except Exception as exc:
                logger.error("Error inspecting %s: %s", task["route"], exc)
                task_res["error"] = str(exc)
            finally:
                bm.close_context(context)
                time.sleep(2.0)

            results.append(task_res)

        out_json = OUT_DIR / "diagnostic_results.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        logger.info("Saved diagnostic results to %s", out_json)

    finally:
        bm.close()


if __name__ == "__main__":
    diagnose()
