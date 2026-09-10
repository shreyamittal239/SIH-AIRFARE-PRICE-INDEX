"""Proof of concept browser test for Playwright automation.

Demonstrates launching Chromium, opening a public test page (https://example.com),
inspecting the title and URL, and closing cleanly.
Can be run standalone via Python or through pytest.
"""

import logging
import sys
from backend.collectors.playwright.browser import BrowserManager

# Configure standard logging for standalone and test runs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test_browser")

TEST_URL = "https://example.com"


def run_browser_test(headless: bool = False) -> dict:
    """Execute the browser proof-of-concept verification.

    Args:
        headless: Whether to run without a visual window. Defaults to False
                  so the user can visually confirm browser operation.

    Returns:
        Dictionary containing page metadata and verification status.
    """
    logger.info("=== Starting Playwright Browser Test ===")
    logger.info("Target URL: %s | Headless: %s", TEST_URL, headless)

    results = {}
    manager = BrowserManager(headless=headless)

    try:
        # 1. Launch Chromium
        manager.launch()
        assert manager.is_running, "Browser failed to launch or is not connected."

        # 2. Open new page & navigate
        page = manager.new_page()
        logger.info("Navigating to %s...", TEST_URL)
        response = page.goto(TEST_URL)

        # 3. Read page title & 4. Read current URL
        title = page.title()
        url = page.url
        status_code = response.status if response else None

        # Print directly to stdout as required by Step 5
        print(f"\n--- BROWSER TEST RESULTS ---")
        print(f"Page Title  : {title}")
        print(f"Current URL : {url}")
        print(f"HTTP Status : {status_code}")
        print(f"----------------------------\n")

        # 5. Demonstrate that Playwright can successfully control the browser
        heading_text = page.locator("h1").inner_text()
        print(f"Heading Text: '{heading_text}'")

        assert "Example" in title, f"Unexpected page title: {title}"
        assert "example.com" in url, f"Unexpected page URL: {url}"
        assert len(heading_text) > 0, "Failed to read h1 element from page."

        logger.info(
            "Browser control verified successfully: Title='%s', URL='%s'",
            title,
            url,
        )

        results = {
            "success": True,
            "title": title,
            "url": url,
            "status_code": status_code,
            "heading": heading_text,
        }
        return results

    except Exception as err:
        logger.error("Browser test failed with error: %s", err, exc_info=True)
        raise
    finally:
        # 6. Close the browser cleanly
        logger.info("Closing browser session cleanly...")
        manager.close()
        logger.info("=== Playwright Browser Test Completed ===")


def test_playwright_browser_launch():
    """Pytest test case verifying browser launch, navigation, and cleanup."""
    # Run test (reads PLAYWRIGHT_HEADLESS env var or defaults to False)
    result = run_browser_test()
    assert result["success"] is True
    assert "example.com" in result["url"]


if __name__ == "__main__":
    # Allow passing '--headless' flag or rely on default headless=False
    use_headless = "--headless" in sys.argv
    try:
        run_browser_test(headless=use_headless)
        print("SUCCESS: Playwright browser test finished cleanly.")
        sys.exit(0)
    except Exception as exc:
        print(f"FAILURE: Browser test encountered an error: {exc}", file=sys.stderr)
        sys.exit(1)
