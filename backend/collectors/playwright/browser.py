"""Browser management module for Playwright automation.

Handles launching, configuring, and closing Playwright browser instances
and contexts cleanly.
"""

import logging
import os
from typing import Optional
from playwright.sync_api import (
    Playwright,
    Browser,
    BrowserContext,
    Page,
    sync_playwright,
    Error as PlaywrightError,
)

logger = logging.getLogger(__name__)


def _get_bool_env(key: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class BrowserManager:
    """Manages the lifecycle of a Playwright browser instance and its contexts."""

    def __init__(
        self,
        headless: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
        browser_name: Optional[str] = None,
    ) -> None:
        """Initialize browser manager configuration.

        Args:
            headless: Whether to run the browser in headless mode.
                      Defaults to PLAYWRIGHT_HEADLESS env var, or False.
            timeout_ms: Default timeout in milliseconds for navigation/operations.
                        Defaults to PLAYWRIGHT_TIMEOUT env var, or 30000.
            browser_name: Browser engine ("chromium", "firefox", "webkit").
                          Defaults to PLAYWRIGHT_BROWSER env var, or "chromium".
        """
        if headless is not None:
            self.headless = headless
        else:
            self.headless = _get_bool_env("PLAYWRIGHT_HEADLESS", False)

        if timeout_ms is not None:
            self.timeout_ms = timeout_ms
        else:
            self.timeout_ms = int(os.getenv("PLAYWRIGHT_TIMEOUT", "30000"))

        self.browser_name = (
            browser_name or os.getenv("PLAYWRIGHT_BROWSER", "chromium")
        ).lower()

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._contexts: list[BrowserContext] = []

    @property
    def is_running(self) -> bool:
        """Check if browser instance is currently launched and connected."""
        return self._browser is not None and self._browser.is_connected()

    def launch(self) -> Browser:
        """Launch the browser instance using the configured engine.

        Returns:
            The launched Browser instance.
        """
        if self.is_running and self._browser is not None:
            logger.debug("Browser is already running.")
            return self._browser

        logger.info(
            "Browser starting: engine=%s, headless=%s, default_timeout=%dms",
            self.browser_name,
            self.headless,
            self.timeout_ms,
        )

        try:
            self._playwright = sync_playwright().start()
            browser_type = getattr(self._playwright, self.browser_name)
            self._browser = browser_type.launch(headless=self.headless)
            logger.info("Browser launched successfully.")
            return self._browser
        except Exception as err:
            logger.error("Failed to start browser: %s", err)
            self.close()
            raise

    def new_context(self, **kwargs) -> BrowserContext:
        """Create a new isolated browser context.

        Args:
            **kwargs: Additional parameters forwarded to browser.new_context().

        Returns:
            The created BrowserContext.
        """
        if not self.is_running or self._browser is None:
            self.launch()
            assert self._browser is not None

        context = self._browser.new_context(**kwargs)
        context.set_default_timeout(self.timeout_ms)
        self._contexts.append(context)
        logger.debug("Browser context created.")
        return context

    def new_page(self, context: Optional[BrowserContext] = None) -> Page:
        """Open a new browser page.

        Args:
            context: Optional context to open the page in. If None,
                     a new context will be created.

        Returns:
            The created Page.
        """
        target_context = context or self.new_context()
        page = target_context.new_page()
        page.set_default_timeout(self.timeout_ms)
        logger.info("Page opened.")
        return page

    def close(self) -> None:
        """Cleanly close all open pages, contexts, browser, and Playwright session."""
        logger.info("Browser closing...")

        for ctx in self._contexts:
            try:
                ctx.close()
            except Exception as err:
                logger.debug("Error closing context: %s", err)
        self._contexts.clear()

        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    self._browser.close()
            except Exception as err:
                logger.debug("Error closing browser: %s", err)
            finally:
                self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as err:
                logger.debug("Error stopping Playwright: %s", err)
            finally:
                self._playwright = None

        logger.info("Browser closed cleanly.")

    def __enter__(self) -> "BrowserManager":
        """Context manager entrypoint."""
        self.launch()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exitpoint with automatic resource cleanup."""
        self.close()
