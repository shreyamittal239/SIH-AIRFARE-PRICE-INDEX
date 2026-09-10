import sys
import json
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def inspect_aix_results():
    # Let's test DEL to BLR (Air India Express has its main hub at BLR and frequent daily direct flights from DEL)
    # And DEL to BOM
    test_urls = [
        ("DEL-BLR", "https://www.airindiaexpress.com/flight-availability?/DEL/BLR/2026-09-17/N/1/0/0/0/0/0/0/O/N/INR/ST/0"),
        ("DEL-BOM", "https://www.airindiaexpress.com/flight-availability?/DEL/BOM/2026-09-17/N/1/0/0/0/0/0/0/O/N/INR/ST/0"),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # Capture XHR/Fetch API responses
        api_responses = {}
        def handle_response(response):
            if any(k in response.url.lower() for k in ["availability", "flight", "search", "fare", "booking"]):
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        api_responses[response.url] = response.text()[:1000]
                except Exception:
                    pass

        page.on("response", handle_response)

        for label, url in test_urls:
            print(f"\n--- Testing route {label}: {url} ---")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)

            # Check if there is an "Ok" button for the popup
            ok_btn = page.locator('button:has-text("Ok"), button:has-text("OK"), .btn:has-text("Ok")').first
            if ok_btn.is_visible():
                print("Found 'Ok' popup button. Clicking...")
                ok_btn.click()
                page.wait_for_timeout(2000)

            print(f"Page title: {page.title()}")
            text = page.locator("body").inner_text()
            print(f"Page text (first 600 chars):\n{text[:600]}")

            # Check for flight cards
            cards = page.evaluate("""() => {
                const nodes = Array.from(document.querySelectorAll('*')).filter(el => {
                    const t = el.innerText || '';
                    return (t.includes('IX ') || t.includes('IX-') || t.includes('Air India Express')) && t.includes('₹') && el.clientHeight > 40 && el.clientWidth > 200;
                });
                return nodes.slice(0, 5).map(n => ({
                    tag: n.tagName,
                    className: n.className,
                    text: n.innerText.replace(/\\n/g, ' -- ')
                }));
            }""")
            print(f"Found {len(cards)} card elements on {label}:")
            for c in cards:
                print("  ", c)

            if cards:
                break

        print(f"\nCaptured {len(api_responses)} JSON API responses:")
        for u, snip in list(api_responses.items())[:5]:
            print(f"URL: {u}\nSnippet: {snip}\n")

        browser.close()

if __name__ == "__main__":
    inspect_aix_results()
