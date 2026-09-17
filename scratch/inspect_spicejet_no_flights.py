import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.collectors.playwright.browser import BrowserManager

def inspect():
    bm = BrowserManager(headless=False, timeout_ms=35000)
    try:
        p = bm.new_page()
        url = "https://www.spicejet.com/search?from=BLR&to=DEL&tripType=1&departure=2026-09-17&adult=1"
        print("Navigating to:", url)
        p.goto(url, wait_until="domcontentloaded", timeout=35000)
        p.wait_for_timeout(6000)
        
        # Check text
        body_text = p.locator("body").inner_text()
        with open("scratch/spicejet_no_flights_text.txt", "w", encoding="utf-8") as f:
            f.write(body_text)
            
        print("Written scratch/spicejet_no_flights_text.txt. Length:", len(body_text))
        
        # Check specific selectors or headings
        headings = p.evaluate('''() => {
            return Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, div, span, p'))
                .map(el => el.innerText ? el.innerText.trim() : '')
                .filter(t => t.toLowerCase().includes('flight') || t.toLowerCase().includes('sorry') || t.toLowerCase().includes('available') || t.toLowerCase().includes('direct') || t.toLowerCase().includes('modify'));
        }''')
        print("Candidate elements:", list(set(headings))[:20])
    finally:
        bm.close()

if __name__ == "__main__":
    inspect()
