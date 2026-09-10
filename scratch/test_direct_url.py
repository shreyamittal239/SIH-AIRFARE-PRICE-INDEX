import sys
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def test_direct_url():
    direct_url = "https://www.airindiaexpress.com/flight-availability?/DEL/BOM/2026-09-17/N/1/0/0/0/0/0/0/O/N/INR/ST/0"
    print(f"Testing direct search URL: {direct_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # Listen to network responses to see if there is an API call for flight availability
        api_calls = []
        page.on("response", lambda resp: api_calls.append({
            "url": resp.url,
            "status": resp.status,
            "contentType": resp.headers.get("content-type", "")
        }) if any(k in resp.url.lower() for k in ["avail", "flight", "search", "fare", "quote", "schedule"]) else None)

        resp = page.goto(direct_url, wait_until="domcontentloaded", timeout=45000)
        print(f"Direct URL HTTP status: {resp.status if resp else 'None'}")
        print("Waiting 10s for flight results rendering...")
        page.wait_for_timeout(10000)

        print(f"Final URL: {page.url}")
        print(f"Page Title: {page.title()}")

        # Check interesting API calls
        print(f"\nInteresting API calls ({len(api_calls)}):")
        for ac in api_calls[:15]:
            print(" ", ac)

        # Inspect DOM for text, flight cards, flight numbers, fares
        dom_details = page.evaluate("""() => {
            const bodyText = document.body.innerText || '';
            const allElements = Array.from(document.querySelectorAll('*'));
            
            // Search for flight numbers (IX or AI or other)
            const ixMatches = bodyText.match(/(?:IX|AI|I5)\\s*\\d{3,4}/gi) || [];
            // Search for fares
            const fareMatches = bodyText.match(/₹\\s*[\\d,]+(?:\\.\\d{2})?/g) || [];
            
            // Find elements that look like cards
            const cardCandidates = allElements.filter(el => {
                const t = el.innerText || '';
                return /(?:IX|AI)\\s*\\d{3,4}/i.test(t) && t.includes('₹') && el.children.length >= 3 && el.clientHeight > 50;
            });
            
            return {
                textLength: bodyText.length,
                ixMatches: [...new Set(ixMatches)],
                fareMatches: fareMatches.slice(0, 15),
                cardCandidatesCount: cardCandidates.length,
                firstCardText: cardCandidates.length > 0 ? cardCandidates[0].innerText.replace(/\\n/g, ' -- ') : null,
                bodySnippet: bodyText.substring(0, 500).replace(/\\n/g, ' ')
            };
        }""")

        print("\n=== DOM EVALUATION ===")
        for k, v in dom_details.items():
            print(f"  {k}: {v}")

        browser.close()

if __name__ == "__main__":
    test_direct_url()
