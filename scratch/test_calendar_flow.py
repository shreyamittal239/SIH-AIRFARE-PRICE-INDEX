import sys
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def test_calendar_flow():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.goto("https://www.airindiaexpress.com/home", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        # 1. Select Origin: DEL
        print("Selecting origin DEL...")
        page.locator('#new-origin-destination-container-desktop [data-testid="source-field"]').click()
        page.wait_for_timeout(500)
        page.locator('#basic-url-origin').fill("DEL")
        page.wait_for_timeout(500)
        page.locator('.flight-list-group-item:has-text("DEL")').first.click()
        page.wait_for_timeout(500)

        # 2. Select Destination: BOM
        print("Selecting destination BOM...")
        dest_field = page.locator('#new-origin-destination-container-desktop [data-testid="destination-field"]')
        if dest_field.is_visible():
            dest_field.click()
            page.wait_for_timeout(500)
        dest_input = page.locator('#basic-url-destination').first
        if dest_input.is_visible():
            dest_input.fill("BOM")
            page.wait_for_timeout(500)
            page.locator('.flight-list-group-item:has-text("BOM")').first.click()
            page.wait_for_timeout(1000)

        # 3. Calendar should be open now
        cal_html = page.evaluate("""() => {
            const cal = document.querySelector('#new-date-selection-container-desktop #calender-data') || document.querySelector('#calender-data');
            if (!cal) return { found: false };
            return {
                found: true,
                className: cal.className,
                innerHTML: cal.innerHTML.substring(0, 800),
                innerText: cal.innerText.replace(/\\n/g, ' ')
            };
        }""")
        print("\nCalendar info:")
        print("Found:", cal_html["found"])
        print("Text:", cal_html.get("innerText"))

        # Check for day buttons
        days = page.evaluate("""() => {
            const dayEls = Array.from(document.querySelectorAll('#calender-data .new-day, #calender-data .day'));
            return dayEls.slice(0, 15).map(d => ({
                id: d.id,
                className: d.className,
                text: d.innerText.trim(),
                disabled: d.classList.contains('disabled')
            }));
        }""")
        print(f"\nFound {len(days)} sample days:")
        for d in days:
            print(" ", d)

        # Click the first enabled day
        first_enabled_day = page.locator('#calender-data .new-day:not(.disabled), #calender-data .day:not(.disabled)').first
        if first_enabled_day.is_visible():
            day_text = first_enabled_day.inner_text().strip()
            print(f"\nClicking first enabled day: '{day_text}'")
            first_enabled_day.click()
            page.wait_for_timeout(1000)

        # Check search button
        search_state = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button.btn-flight'));
            return btns.map(b => {
                const rect = b.getBoundingClientRect();
                return {
                    text: b.innerText.trim(),
                    rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                    visible: rect.width > 0 && rect.height > 0
                };
            });
        }""")
        print("\nSearch button state after selecting date:")
        for bs in search_state:
            print(" ", bs)

        # If any button has rect width > 0, click it!
        btn_to_click = page.locator('button.btn-flight').filter(has_text="Search Flights").first
        print("Clicking Search Flights...")
        # Try forced click or JS click if obscured
        page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button.btn-flight'));
            const b = btns.find(x => x.innerText.includes('Search Flights')) || btns[0];
            if (b) b.click();
        }""")

        print("Waiting 10s to see where navigation goes...")
        page.wait_for_timeout(10000)
        print("Final URL:", page.url)
        print("Final Title:", page.title())

        browser.close()

if __name__ == "__main__":
    test_calendar_flow()
