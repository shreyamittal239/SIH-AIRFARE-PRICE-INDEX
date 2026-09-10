import sys
import os
from datetime import date, timedelta
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def test_search_flow():
    today = date.today()
    target_date = today + timedelta(days=7)
    target_day = str(target_date.day)

    print(f"Target date: {target_date} (Day: {target_day})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.goto("https://www.airindiaexpress.com/home", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        # 1. Select One Way
        print("Selecting 'One Way'...")
        one_way = page.locator('#new-search-widget-container div[id="One Way"]').first
        if one_way.is_visible():
            one_way.click()
            page.wait_for_timeout(500)

        # 2. Select Origin
        print("Opening origin selector...")
        page.locator('#new-origin-destination-container-desktop [data-testid="source-field"]').click()
        page.wait_for_timeout(1000)

        origin_input = page.locator('#basic-url-origin').first
        if origin_input.is_visible():
            origin_input.fill("DEL")
            page.wait_for_timeout(1000)
            match_del = page.locator('.flight-list-group-item:has-text("DEL"), .flight-list-group-item-right-section:has-text("DEL")').first
            match_del.click()
            print("Selected DEL origin.")
            page.wait_for_timeout(1000)

        # 3. Destination
        dest_input = page.locator('#basic-url-destination').first
        if not dest_input.is_visible():
            print("Opening destination selector...")
            page.locator('#new-origin-destination-container-desktop [data-testid="destination-field"]').click()
            page.wait_for_timeout(1000)

        if dest_input.is_visible():
            dest_input.fill("BOM")
            page.wait_for_timeout(1000)
            match_bom = page.locator('.flight-list-group-item:has-text("BOM"), .flight-list-group-item-right-section:has-text("BOM")').first
            match_bom.click()
            print("Selected BOM destination.")
            page.wait_for_timeout(1000)

        # 4. Date Selection
        cal_desktop = page.locator('#new-date-selection-container-desktop #calender-data')
        if not cal_desktop.is_visible():
            print("Opening calendar...")
            page.locator('#new-date-selection-container-desktop #start-date-input-button').click()
            page.wait_for_timeout(1000)

        print(f"Selecting day {target_day} in desktop calendar...")
        day_btn = cal_desktop.locator(f'div.new-day[id="{target_day}"]:not(.disabled)').first
        if day_btn.is_visible():
            day_btn.click()
            print(f"Clicked day {target_day}.")
        else:
            flipper = cal_desktop.locator('.flipper-button.right').first
            if flipper.is_visible():
                flipper.click()
                page.wait_for_timeout(500)
                day_btn = cal_desktop.locator(f'div.new-day[id="{target_day}"]:not(.disabled)').first
                if day_btn.is_visible():
                    day_btn.click()
                    print(f"Clicked day {target_day} in next month.")

        page.wait_for_timeout(1000)

        # Let's inspect all search buttons and their visibility
        buttons_info = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button.btn-flight'));
            return btns.map((b, idx) => {
                const rect = b.getBoundingClientRect();
                const style = window.getComputedStyle(b);
                return {
                    index: idx,
                    text: b.innerText.trim(),
                    display: style.display,
                    visibility: style.visibility,
                    rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                    parentClass: b.parentElement ? b.parentElement.className : ''
                };
            });
        }""")
        print("Search buttons state:")
        for bi in buttons_info:
            print(" ", bi)

        # Click the visible button!
        visible_btn = page.locator('button.btn-flight:visible').first
        if visible_btn.count() > 0 and visible_btn.is_visible():
            print("Clicking visible search button with locator: button.btn-flight:visible")
            visible_btn.click()
        else:
            print("No button found with :visible, attempting JS click on button with non-zero dimensions...")
            page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button.btn-flight'));
                const b = btns.find(btn => btn.getBoundingClientRect().width > 0) || btns[0];
                if (b) b.click();
            }""")

        print("Waiting 12s for search results / navigation...")
        page.wait_for_timeout(12000)

        print(f"Current URL after search: {page.url}")
        print(f"Page Title after search: {page.title()}")

        # Look for flight cards or results
        results_info = page.evaluate("""() => {
            const url = window.location.href;
            const title = document.title;
            const allText = document.body.innerText || '';
            const ixMatches = allText.match(/IX\\s*\\d{3,4}/g) || [];
            const fareMatches = allText.match(/₹\\s*[\\d,]+/g) || [];
            
            // Check for flight cards
            const cards = Array.from(document.querySelectorAll('[class*="flight-card"], [class*="flight-item"], [class*="flight-details"], [class*="bound-table"], [data-testid*="flight"]'));
            
            return {
                url,
                title,
                ixCount: ixMatches.length,
                ixSample: ixMatches.slice(0, 10),
                fareCount: fareMatches.length,
                fareSample: fareMatches.slice(0, 10),
                cardCount: cards.length,
                textLength: allText.length,
                firstCardText: cards.length > 0 ? cards[0].innerText.substring(0, 200).replace(/\\n/g, ' -- ') : null
            };
        }""")
        print("\n=== RESULTS INFO ===")
        for k, v in results_info.items():
            print(f"  {k}: {v}")

        # If no specific flight cards found yet, let's dump DOM elements containing 'IX'
        if results_info.get("ixCount", 0) > 0 and results_info.get("cardCount", 0) == 0:
            sample_elements = page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('*'));
                const matched = all.filter(el => {
                    const t = el.innerText || '';
                    return /^IX\\s*\\d{3,4}$/.test(t.trim());
                });
                return matched.map(el => ({
                    tag: el.tagName,
                    id: el.id,
                    className: el.className,
                    parentClass: el.parentElement ? el.parentElement.className : '',
                    grandParentClass: el.parentElement && el.parentElement.parentElement ? el.parentElement.parentElement.className : '',
                    containerText: el.parentElement && el.parentElement.parentElement ? el.parentElement.parentElement.innerText.substring(0, 250).replace(/\\n/g, ' -- ') : ''
                }));
            }""")
            print("\n=== IX ELEMENT CONTAINERS ===")
            for se in sample_elements[:5]:
                print(" ", se)

        browser.close()

if __name__ == "__main__":
    test_search_flow()
