import sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collectors.playwright.browser import BrowserManager

bm = BrowserManager(headless=True)
try:
    page = bm.new_page()
    url = "https://www.cleartrip.com/flights/results?from=BLR&to=DEL&depart_date=23/09/2026&adults=1&childs=0&infants=0&class=Economy&airline=&carrier=&intl=n&page=loaded"
    page.goto(url, wait_until="domcontentloaded")
    
    # Check what .bg-white button returns immediately
    btns = page.evaluate("""() => Array.from(document.querySelectorAll('.bg-white button')).map(b => b.innerText.trim())""")
    print("Buttons inside .bg-white immediately:", btns)
    
    # Check what flight card buttons actually have
    book_btns = page.evaluate("""() => Array.from(document.querySelectorAll('button')).filter(b => b.innerText.includes('Book')).map(b => b.innerText.trim())""")
    print("Book buttons immediately:", len(book_btns))

    page.wait_for_timeout(5000)
    book_btns_after = page.evaluate("""() => Array.from(document.querySelectorAll('button')).filter(b => b.innerText.includes('Book')).map(b => b.innerText.trim())""")
    print("Book buttons after 5s:", len(book_btns_after))

finally:
    bm.close()
