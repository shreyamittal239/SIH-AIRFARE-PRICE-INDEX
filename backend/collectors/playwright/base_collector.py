"""Base collector abstraction for airline and OTA portals.

Defines the common lifecycle and navigation contract for all future
Playwright-based data collectors in the SIH Airfare Price Index project.
"""

from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Optional
from playwright.sync_api import Page, Response, Error as PlaywrightError

from backend.collectors.playwright.browser import BrowserManager

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """Abstract base collector for web data collection.

    Manages browser/page lifecycle and provides standard navigation.
    Subclasses will implement portal-specific extraction logic.
    """

    def __init__(
        self,
        source_name: str,
        browser_manager: Optional[BrowserManager] = None,
    ) -> None:
        """Initialize the base collector.

        Args:
            source_name: Identifier for the data source (e.g., 'indigo', 'makemytrip').
            browser_manager: Optional existing BrowserManager instance. If not provided,
                             a new one will be initialized.
        """
        self.source_name = source_name
        self.browser_manager = browser_manager or BrowserManager()
        self._owns_browser_manager = browser_manager is None
        self.page: Optional[Page] = None

    def start(self) -> Page:
        """Initialize browser and create a working page."""
        if not self.browser_manager.is_running:
            self.browser_manager.launch()
        self.page = self.browser_manager.new_page()
        return self.page

    def navigate(self, url: str) -> Optional[Response]:
        """Navigate to a target URL with standard logging and error handling.

        Args:
            url: Target URL to load.

        Returns:
            The main resource response, or None if navigation failed.
        """
        if self.page is None:
            self.start()
            assert self.page is not None

        logger.info("[%s] Navigating to: %s", self.source_name, url)
        try:
            response = self.page.goto(url)
            title = self.page.title()
            logger.info(
                "[%s] Navigation successful: url=%s, title='%s'",
                self.source_name,
                self.page.url,
                title,
            )
            return response
        except PlaywrightError as err:
            logger.error(
                "[%s] Navigation failed for %s: %s",
                self.source_name,
                url,
                err,
            )
            raise

    def close(self) -> None:
        """Clean up page and browser manager resources."""
        if self.page is not None:
            try:
                ctx = self.page.context
                self.page.close()
                if ctx is not None and hasattr(self.browser_manager, "close_context"):
                    self.browser_manager.close_context(ctx)
            except Exception as err:
                logger.debug("[%s] Error closing page/context: %s", self.source_name, err)
            finally:
                self.page = None

        if self._owns_browser_manager:
            self.browser_manager.close()

    def __enter__(self) -> "BaseCollector":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit with resource cleanup."""
        self.close()

    @abstractmethod
    def extract_fares(self, page: Page) -> List[Dict[str, Any]]:
        """Extract raw fare observations from the loaded portal page.

        To be implemented by portal-specific collectors (e.g., IndigoCollector).
        Should yield raw data structures aligned with FareObservation fields.
        Must NOT calculate indexes, weights, or statistical aggregations.

        Args:
            page: Playwright Page instance with loaded flight results.

        Returns:
            List of raw fare observation dictionaries.
        """
        raise NotImplementedError("Subclasses must implement extract_fares()")
