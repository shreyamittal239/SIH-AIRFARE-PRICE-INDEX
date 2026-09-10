import sys
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def inspect_calendar():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.goto("https://www.airindiaexpress.com/home", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        # 1. One Way
        page.locator('#new-search-widget-container div[id="One Way"]').first.click()
        page.wait_for_timeout(500)

        # 2. Origin
        page.locator('#new-origin-destination-container-desktop [data-testid="source-field"]').click()
        page.wait_for_timeout(500)
        page.locator('#basic-url-origin').first.fill("DEL")
        page.wait_for_timeout(500)
        page.locator('.flight-list-group-item-right-section:has-text("DEL")').first.click()
        page.wait_for_timeout(500)

        # 3. Destination
        page.locator('#new-origin-destination-container-desktop [data-testid="destination-field"]').click()
        page.wait_for_timeout(500)
        page.locator('#basic-url-destination').first.fill("BOM")
        page.wait_for_timeout(500)
        page.locator('.flight-list-group-item-right-section:has-text("BOM")').first.click()
        page.wait_for_timeout(500)

        # 4. Now inspect what is on screen regarding calendar and search buttons
        state_after_dest = page.evaluate("""() => {
            const cal = document.querySelector('#new-date-selection-container-desktop #calender-data');
            const btns = Array.from(document.querySelectorAll('button, div[role="button"]')).filter(b => {
                const rect = b.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            });
            return {
                calendarVisible: cal ? cal.offsetParent !== null : false,
                calendarText: cal ? cal.innerText.replace(/\\n/g, ' ').substring(0, 200) : null,
                visibleButtons: btns.map(b => ({
                    tag: b.tagName,
                    id: b.id,
                    className: b.className,
                    text: (b.innerText || '').trim().replace(/\\n/g, ' ').substring(0, 40)
                }))
            };
        }""")
        print("State after selecting destination:")
        print("Calendar visible:", state_after_dest["calendarVisible"])
        print("Calendar text:", state_after_dest["calendarText"])
        print("Visible buttons:")
        for b in state_after_dest["visibleButtons"]:
            if any(k in b["text"].lower() for k in ["search", "done", "continue", "date", "flight"]):
                print(" ", b)

        # 5. Click Day 17
        cal = page.locator('#new-date-selection-container-desktop #calender-data')
        day17 = cal.locator('div.new-day[id="17"]:not(.disabled)').first
        if day17.is_visible():
            print("Clicking day 17...")
            day17.click()
            page.wait_for_timeout(1000)

        # Inspect state after clicking day 17
        state_after_day = page.evaluate("""() => {
            const cal = document.querySelector('#new-date-selection-container-desktop #calender-data');
            const btns = Array.from(document.querySelectorAll('button, div[role="button"], a[role="button"]')).filter(b => {
                const rect = b.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            });
            return {
                calendarVisible: cal ? cal.offsetParent !== null : false,
                visibleButtons: btns.map(b => ({
                    tag: b.tagName,
                    id: b.id,
                    className: b.className,
                    text: (b.innerText || '').trim().replace(/\\n/g, ' ').substring(0, 40)
                }))
            };
        }""")
        print("\nState after clicking day 17:")
        print("Calendar visible:", state_after_day["calendarVisible"])
        print("Visible buttons:")
        for b in state_after_day["visibleButtons"]:
            if any(k in b["text"].lower() for k in ["search", "done", "continue", "date", "flight"]):
                print(" ", b)

        # If Search Flights button is visible, what is its exact selector?
        search_btns = page.locator('button:has-text("Search Flights"), [role="button"]:has-text("Search Flights")')
        print(f"Total Search Flights locators: {search_btns.count()}")
        for i in range(search_btns.count()):
            btn = search_btns.nth(i)
            print(f"  btn {i}: is_visible={btn.is_visible()}, box={btn.bounding_box()}")

        browser.close()

if __name__ == "__main__":
    inspect_calendar()
