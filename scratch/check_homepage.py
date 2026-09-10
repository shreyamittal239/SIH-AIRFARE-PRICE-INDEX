import sys
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def check_homepage_state():
    with sync_playwright() as p:
        # Use headful or normal browser
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.goto("https://www.airindiaexpress.com/home", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)

        title = page.title()
        url = page.url
        body_text = page.locator("body").inner_text()

        print(f"Title: {title}")
        print(f"URL: {url}")
        print(f"Body text (first 500 chars):\n{body_text[:500]}")

        # Check for 'Take a Break' popup
        has_break = "Take a Break" in body_text or "tried a few times" in body_text
        print(f"\nHas 'Take a Break' anti-bot notice: {has_break}")

        browser.close()

if __name__ == "__main__":
    check_homepage_state()
