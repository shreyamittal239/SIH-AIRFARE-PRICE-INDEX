"""Playwright browser automation package for SIH Airfare Price Index."""

from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.base_collector import BaseCollector
from backend.collectors.playwright.yatra_collector import YatraCollector

__all__ = ["BrowserManager", "BaseCollector", "YatraCollector"]
