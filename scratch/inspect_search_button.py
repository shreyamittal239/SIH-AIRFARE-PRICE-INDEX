import sys
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def inspect_airport_selection():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.goto("https://www.airindiaexpress.com/home", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        print("Clicking desktop source-field...")
        page.locator('#new-origin-destination-container-desktop [data-testid="source-field"]').click()
        page.wait_for_timeout(1500)

        # Look for search input in airport selector dialog
        inputs = page.evaluate("""() => {
            const allInputs = Array.from(document.querySelectorAll('input'));
            return allInputs.filter(i => i.offsetParent !== null).map(i => ({
                id: i.id,
                placeholder: i.placeholder,
                className: i.className,
                type: i.type,
                name: i.name
            }));
        }""")
        print("Visible inputs after opening source dropdown:")
        for inp in inputs:
            print(" ", inp)

        # Look for airport/city list items
        items = page.evaluate("""() => {
            const listItems = Array.from(document.querySelectorAll('.airport-details, .airport-code, [class*="city-name"], [class*="station"], .station-details, [class*="airport-list"] li'));
            return listItems.slice(0, 10).map(li => ({
                tag: li.tagName,
                className: li.className,
                text: (li.innerText || '').replace(/\\n/g, ' - ')
            }));
        }""")
        print("Visible airport items:")
        for it in items:
            print(" ", it)

        # Let's type 'DEL' in the active/focused input or search input
        search_input = page.locator('input[placeholder*="city" i], input[placeholder*="airport" i], input[placeholder*="Search" i]').first
        if search_input.is_visible():
            print("Typing DEL into airport search input...")
            search_input.fill("DEL")
            page.wait_for_timeout(1500)

            # Check matching results
            matches = page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('div, li, p, span'));
                const filtered = els.filter(el => {
                    const t = el.innerText || '';
                    return (t.includes('DEL') || t.includes('Delhi')) && t.length < 100 && el.children.length <= 2;
                });
                return filtered.slice(0, 8).map(el => ({
                    tag: el.tagName,
                    className: el.className,
                    text: el.innerText.replace(/\\n/g, ' - ')
                }));
            }""")
            print("Matches for 'DEL':")
            for m in matches:
                print(" ", m)

        browser.close()

if __name__ == "__main__":
    inspect_airport_selection()
