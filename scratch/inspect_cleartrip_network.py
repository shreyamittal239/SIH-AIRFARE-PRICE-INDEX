import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collectors.playwright.browser import BrowserManager

bm = BrowserManager(headless=True)
try:
    page = bm.new_page()
    urls = []
    def on_response(res):
        urls.append((res.status, res.url))

    page.on("response", on_response)
    target_url = "https://www.cleartrip.com/flights/results?from=DEL&to=BOM&depart_date=23/09/2026&adults=1&childs=0&infants=0&class=Economy&airline=&carrier=&intl=n&page=loaded"
    print(f"Navigating to {target_url}...")
    page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    print("Page title:", page.title())
    
    # Wait 10 seconds and see what network traffic occurs
    page.wait_for_timeout(10000)
    print(f"Total responses intercepted: {len(urls)}")
    print("Responses containing 'flight' or 'search' or 'api':")
    for status, u in urls:
        if any(k in u.lower() for k in ["flight", "search", "api", "v2", "fare", "card"]):
            print(f"  [{status}] {u[:120]}")

    # Check page text
    body_text = page.locator("body").inner_text()
    print("Body text preview (first 500 chars):", repr(body_text[:500]))

finally:
    bm.close()
