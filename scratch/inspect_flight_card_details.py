import sys
import json
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def inspect_flight_card_details():
    # Test routes where Air India Express operates high-frequency daily flights
    # DEL -> BLR is one of AIX's primary trunk routes
    # Also test DEL -> BOM
    routes = [
        ("DEL-BLR", "https://www.airindiaexpress.com/flight-availability?/DEL/BLR/2026-09-17/N/1/0/0/0/0/0/0/O/N/INR/ST/0"),
        ("BLR-DEL", "https://www.airindiaexpress.com/flight-availability?/BLR/DEL/2026-09-17/N/1/0/0/0/0/0/0/O/N/INR/ST/0"),
        ("DEL-BOM", "https://www.airindiaexpress.com/flight-availability?/DEL/BOM/2026-09-15/N/1/0/0/0/0/0/0/O/N/INR/ST/0"),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        for label, url in routes:
            print(f"\n==========================================")
            print(f"Checking {label}: {url}")
            print(f"==========================================")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(8000)

            # Check if rate-limit popup is present and click Ok
            try:
                ok_btn = page.locator('button:has-text("Ok"), button:has-text("OK")').first
                if ok_btn.is_visible():
                    print("Dismissing 'Take a break' modal...")
                    ok_btn.click()
                    page.wait_for_timeout(2000)
            except Exception:
                pass

            data = page.evaluate("""() => {
                const bodyText = document.body.innerText || '';
                
                // 1. Check Date header / carousel
                const dateHeaders = Array.from(document.querySelectorAll('[class*="date"], [class*="carousel"], [class*="strip"], [class*="calendar"]'))
                    .filter(el => el.clientHeight > 20 && el.innerText && /Sep|Oct|2026/i.test(el.innerText))
                    .slice(0, 5)
                    .map(el => ({
                        className: el.className,
                        text: el.innerText.replace(/\\n/g, ' -- ')
                    }));

                // 2. Check for flight card containers
                // Look for nodes containing IX or flight number pattern
                const allNodes = Array.from(document.querySelectorAll('*'));
                const flightNodes = allNodes.filter(el => {
                    const t = el.innerText || '';
                    return /IX\\s*\\d{3,4}/i.test(t) && el.clientHeight > 60 && el.clientWidth > 300 && el.children.length >= 2;
                });

                // Pick the most compact container that holds a full flight quote
                // (i.e. has flight number, times, and fare tiers)
                const fullCards = flightNodes.filter(el => {
                    const t = el.innerText || '';
                    return t.includes('₹') && /\\d{1,2}:\\d{2}/.test(t);
                });

                let sampleCard = null;
                if (fullCards.length > 0) {
                    // Sort by text length ascending to get the innermost card container
                    fullCards.sort((a, b) => a.innerText.length - b.innerText.length);
                    const c = fullCards[0];
                    sampleCard = {
                        tag: c.tagName,
                        className: c.className,
                        id: c.id,
                        innerText: c.innerText,
                        htmlSnippet: c.innerHTML.substring(0, 1500),
                        childCount: c.children.length
                    };
                }

                // 3. Look for fare family names on the page (Xpress Lite, Xpress Value, Xpress Flex, Xpress Biz)
                const fareFamilies = [];
                for (const name of ['Xpress Lite', 'Xpress Value', 'Xpress Flex', 'Xpress Biz', 'Lite', 'Value', 'Flex', 'Biz']) {
                    const match = allNodes.filter(el => (el.innerText || '').trim() === name);
                    if (match.length > 0) {
                        fareFamilies.push({
                            name: name,
                            count: match.length,
                            parentClass: match[0].parentElement ? match[0].parentElement.className : ''
                        });
                    }
                }

                return {
                    bodyLength: bodyText.length,
                    hasSorryNoFlights: bodyText.includes("Sorry, no flights"),
                    dateHeaders: dateHeaders,
                    totalFlightNodes: flightNodes.length,
                    totalFullCards: fullCards.length,
                    sampleCard: sampleCard,
                    fareFamilies: fareFamilies,
                    bodyPreview: bodyText.substring(0, 400).replace(/\\n/g, ' ')
                };
            }""")

            print(f"Body preview: {data['bodyPreview']}")
            print(f"Has 'Sorry no flights': {data['hasSorryNoFlights']}")
            print(f"Date headers found ({len(data['dateHeaders'])}):")
            for dh in data['dateHeaders']:
                print(f"  {dh}")
            print(f"Fare families found: {data['fareFamilies']}")
            print(f"Total full cards: {data['totalFullCards']}")

            if data['sampleCard']:
                print(f"\n--- SAMPLE FLIGHT CARD DOM ---")
                print(f"Class: {data['sampleCard']['className']}")
                print(f"Inner Text:\n{data['sampleCard']['innerText']}")
                print(f"\nHTML Snippet:\n{data['sampleCard']['htmlSnippet']}")
                break

        browser.close()

if __name__ == "__main__":
    inspect_flight_card_details()
