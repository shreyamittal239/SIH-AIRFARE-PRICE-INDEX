"""Playwright browser automation package for SIH Airfare Price Index."""

from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.base_collector import BaseCollector
from backend.collectors.playwright.yatra_collector import YatraCollector
from backend.collectors.playwright.air_india_express_collector import AirIndiaExpressCollector
from backend.collectors.playwright.easemytrip_collector import EaseMyTripCollector
from backend.collectors.playwright.cleartrip_collector import CleartripCollector

__all__ = [
    "BrowserManager",
    "BaseCollector",
    "YatraCollector",
    "AirIndiaExpressCollector",
    "EaseMyTripCollector",
    "CleartripCollector",
]

