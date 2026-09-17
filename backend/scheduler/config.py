"""Configuration definitions for the Automated Scraping Scheduler.

Supports configuration via environment variables with safe defaults:
- PROJECT_TIMEZONE: Asia/Kolkata
- SCRAPE_SCHEDULE_HOUR: 2
- SCRAPE_SCHEDULE_MINUTE: 0
- SCRAPE_SOURCES: yatra,easemytrip,spicejet,air_india_express
- SCRAPE_MAX_RUNTIME_MINUTES: 180
- SCRAPE_LOCK_TIMEOUT_SECONDS: 60
- SCRAPE_LOCK_ID: 847291
"""

from dataclasses import dataclass, field
import logging
import os
from typing import List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Known canonical data source codes supported by CollectionOrchestrator
CANONICAL_SOURCE_MAP = {
    "yatra": "YATRA",
    "yatra_ota": "YATRA",
    "easemytrip": "EASEMYTRIP",
    "easemytrip_ota": "EASEMYTRIP",
    "spicejet": "SPICEJET",
    "spicejet_direct": "SPICEJET",
    "air_india_express": "AIR_INDIA_EXPRESS",
    "air_india_express_direct": "AIR_INDIA_EXPRESS",
    "airindiaexpress": "AIR_INDIA_EXPRESS",
    "cleartrip": "CLEARTRIP",
    "cleartrip_ota": "CLEARTRIP",
}

DEFAULT_SOURCES = ["YATRA", "EASEMYTRIP", "SPICEJET", "AIR_INDIA_EXPRESS"]


def parse_source_list(raw: Optional[str]) -> List[str]:
    """Parse comma-separated source string into canonical uppercase source codes.

    Cleartrip is disabled by default and only enabled if explicitly passed.
    """
    if not raw or not raw.strip():
        return list(DEFAULT_SOURCES)

    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    parsed: List[str] = []
    for token in tokens:
        canonical = CANONICAL_SOURCE_MAP.get(token, token.upper())
        if canonical not in parsed:
            parsed.append(canonical)

    return parsed or list(DEFAULT_SOURCES)


@dataclass(frozen=True)
class SchedulerConfig:
    """Immutable configuration for the scraping scheduler."""

    timezone_name: str = "Asia/Kolkata"
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Asia/Kolkata"))
    schedule_hour: int = 2
    schedule_minute: int = 0
    sources: List[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    max_runtime_minutes: int = 180
    lock_timeout_seconds: int = 60
    advisory_lock_id: int = 847291
    base_period_code: str = "2026-07"
    headless: bool = True
    task_delay_seconds: float = 2.0


def get_scheduler_config(
    timezone_name: Optional[str] = None,
    schedule_hour: Optional[int] = None,
    schedule_minute: Optional[int] = None,
    sources_override: Optional[str] = None,
    max_runtime_minutes: Optional[int] = None,
    lock_timeout_seconds: Optional[int] = None,
    advisory_lock_id: Optional[int] = None,
    base_period_code: Optional[str] = None,
    headless: Optional[bool] = None,
    task_delay_seconds: Optional[float] = None,
) -> SchedulerConfig:
    """Load configuration from environment variables with optional parameter overrides."""
    tz_str = timezone_name or os.getenv("PROJECT_TIMEZONE", "Asia/Kolkata")
    try:
        tz_obj = ZoneInfo(tz_str)
    except Exception as exc:
        logger.warning("Invalid timezone '%s' (%s), falling back to 'Asia/Kolkata'", tz_str, exc)
        tz_str = "Asia/Kolkata"
        tz_obj = ZoneInfo("Asia/Kolkata")

    hour = schedule_hour if schedule_hour is not None else int(os.getenv("SCRAPE_SCHEDULE_HOUR", "2"))
    minute = schedule_minute if schedule_minute is not None else int(os.getenv("SCRAPE_SCHEDULE_MINUTE", "0"))

    raw_sources = sources_override if sources_override is not None else os.getenv(
        "SCRAPE_SOURCES", "yatra,easemytrip,spicejet,air_india_express"
    )
    sources = parse_source_list(raw_sources)

    max_runtime = (
        max_runtime_minutes
        if max_runtime_minutes is not None
        else int(os.getenv("SCRAPE_MAX_RUNTIME_MINUTES", "180"))
    )

    lock_timeout = (
        lock_timeout_seconds
        if lock_timeout_seconds is not None
        else int(os.getenv("SCRAPE_LOCK_TIMEOUT_SECONDS", "60"))
    )

    lock_id = (
        advisory_lock_id
        if advisory_lock_id is not None
        else int(os.getenv("SCRAPE_LOCK_ID", "847291"))
    )

    base_period = base_period_code or os.getenv("SCRAPE_BASE_PERIOD", "2026-07")

    is_headless = (
        headless
        if headless is not None
        else os.getenv("SCRAPE_HEADLESS", "true").lower() in ("true", "1", "yes")
    )

    delay = (
        task_delay_seconds
        if task_delay_seconds is not None
        else float(os.getenv("SCRAPE_TASK_DELAY_SECONDS", "2.0"))
    )

    return SchedulerConfig(
        timezone_name=tz_str,
        timezone=tz_obj,
        schedule_hour=hour,
        schedule_minute=minute,
        sources=sources,
        max_runtime_minutes=max_runtime,
        lock_timeout_seconds=lock_timeout,
        advisory_lock_id=lock_id,
        base_period_code=base_period,
        headless=is_headless,
        task_delay_seconds=delay,
    )
