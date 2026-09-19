import sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collectors.playwright.browser import BrowserManager

bm = BrowserManager(headless=True)
try:
    urls_to_test = [
        ("Run 325 exact", "https://www.cleartrip.com/flights/results?from=BLR&to=DEL&depart_date=15/10/2026&adults=1&childs=0&infants=0&class=Economy&airline=&carrier=&intl=n&page=loaded"),
        ("No extra params", "https://www.cleartrip.com/flights/results?from=DEL&to=BOM&depart_date=23/09/2026&adults=1&class=Economy"),
        ("ISO date", "https://www.cleartrip.com/flights/results?from=DEL&to=BOM&depart_date=2026-09-23&adults=1&class=Economy"),
        ("Dash date", "https://www.cleartrip.com/flights/results?from=DEL&to=BOM&depart_date=23-09-2026&adults=1&class=Economy"),
    ]
    for label, url in urls_to_test:
        page = bm.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(4000)
            text = page.locator("body").inner_text()
            stumped = "stumped" in text.lower() or "wrong url" in text.lower()
            book_btns = page.evaluate("() => Array.from(document.querySelectorAll('button')).filter(b => b.innerText && b.innerText.includes('Book')).length")
            print(f"{label}: stumped={stumped}, book_btns={book_btns}, text_len={len(text)}, title='{page.title()}'")
        finally:
            bm.close_context(page.context)
finally:
    bm.close()
