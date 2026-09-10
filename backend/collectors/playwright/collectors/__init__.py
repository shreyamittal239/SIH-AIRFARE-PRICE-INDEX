"""Concrete airline and OTA collectors."""

from backend.collectors.playwright.collectors.first_airline_collector import (
    FirstAirlineCollector,
    SpiceJetCollector,
)

__all__ = ["FirstAirlineCollector", "SpiceJetCollector"]
