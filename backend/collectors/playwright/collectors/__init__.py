"""Concrete airline and OTA collectors."""

from backend.collectors.playwright.collectors.first_airline_collector import (
    FirstAirlineCollector,
    SpiceJetCollector,
)
from backend.collectors.playwright.air_india_express_collector import (
    AirIndiaExpressCollector,
)

__all__ = ["FirstAirlineCollector", "SpiceJetCollector", "AirIndiaExpressCollector"]

