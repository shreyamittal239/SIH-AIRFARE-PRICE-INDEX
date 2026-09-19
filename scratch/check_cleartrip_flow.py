import sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collectors.playwright.browser import BrowserManager

bm = BrowserManager(headless=True)
try:
    page = bm.new_page()
    page.goto("https://www.cleartrip.com/flights", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    
    # Check search buttons
    buttons = page.locator("button").all_inner_texts()
    print("Homepage buttons:", [b.strip() for b in buttons if b.strip()])
    
    # Try finding the search button
    search_btn = page.locator("button:has-text('Search')")
    if search_btn.count() > 0:
        print("Found Search button, clicking...")
        search_btn.first.click()
        page.wait_for_timeout(6000)
        print("Post-click URL:", page.url)
    else:
        print("No Search button found")
finally:
    bm.close()
