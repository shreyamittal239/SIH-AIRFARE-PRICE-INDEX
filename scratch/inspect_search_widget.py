import sys
import json
from playwright.sync_api import sync_playwright

def inspect_search_widget():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.goto("https://www.airindiaexpress.com/home", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)

        # Inspect the whole search widget
        details = page.evaluate("""() => {
            const superDiv = document.querySelector('#flight-search-widget-super-div') || document.querySelector('.new-search-widget-container');
            if (!superDiv) return { error: 'Super div not found' };

            const allElements = Array.from(superDiv.querySelectorAll('*'));
            const interesting = allElements
                .filter(el => el.id || el.getAttribute('data-testid') || el.tagName === 'INPUT' || el.tagName === 'BUTTON' || el.getAttribute('role') === 'button')
                .map(el => ({
                    tag: el.tagName,
                    id: el.id,
                    testid: el.getAttribute('data-testid'),
                    role: el.getAttribute('role'),
                    className: (el.className || '').toString().substring(0, 50),
                    text: (el.innerText || el.value || '').trim().substring(0, 40)
                }));
            return {
                interesting: interesting.slice(0, 60),
                fullText: superDiv.innerText
            };
        }""")

        print("=== SEARCH WIDGET ELEMENTS ===")
        for item in details.get("interesting", []):
            print(item)

        print("\n=== FULL WIDGET TEXT ===")
        print(details.get("fullText", ""))

        browser.close()

if __name__ == "__main__":
    inspect_search_widget()
