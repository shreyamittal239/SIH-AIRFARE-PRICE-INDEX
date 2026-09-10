import sys
import os
import time
from playwright.sync_api import sync_playwright

def inspect_airindiaexpress():
    with sync_playwright() as p:
        # Launch Chromium (headful or headless)
        # Note: websites often behave differently with real user agents
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        print("Navigating to https://www.airindiaexpress.com ...")
        try:
            resp = page.goto("https://www.airindiaexpress.com", wait_until="domcontentloaded", timeout=45000)
            print(f"Status: {resp.status if resp else 'None'}")
            print(f"Page title: {page.title()}")
            print(f"Current URL: {page.url}")

            # Wait a few seconds for hydration
            page.wait_for_timeout(5000)
            print(f"Page title after 5s: {page.title()}")
            print(f"Current URL after 5s: {page.url}")

            # Look for input fields, buttons, testids
            inputs = page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('input, button, [role="button"], [data-testid]'));
                return els.slice(0, 50).map(el => ({
                    tag: el.tagName,
                    type: el.getAttribute('type'),
                    id: el.id,
                    className: el.className,
                    testid: el.getAttribute('data-testid'),
                    ariaLabel: el.getAttribute('aria-label'),
                    role: el.getAttribute('role'),
                    text: (el.innerText || el.value || '').trim().substring(0, 50)
                }));
            }""")
            print(f"Found {len(inputs)} interactive elements on home page:")
            for item in inputs[:30]:
                print(" ", item)

            # Check if there is a search or booking widget or form
            search_widgets = page.evaluate("""() => {
                const widgets = Array.from(document.querySelectorAll('[class*="flight"], [class*="search"], [id*="search"], [class*="booking"]'));
                return widgets.slice(0, 20).map(w => ({
                    tag: w.tagName,
                    id: w.id,
                    className: (w.className || '').toString().substring(0, 80),
                    text: (w.innerText || '').substring(0, 80).replace(/\\n/g, ' ')
                }));
            }""")
            print(f"Potential search widgets: {len(search_widgets)}")
            for sw in search_widgets[:15]:
                print(" ", sw)

        except Exception as e:
            print(f"Error navigating: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    inspect_airindiaexpress()
