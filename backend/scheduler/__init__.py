"""Automated Scraping Scheduler for SIH26056 Airfare Price Index.

Exposes:
- SchedulerConfig, get_scheduler_config: Configuration dataclass and loader
- SchedulerLock: PostgreSQL advisory lock for cycle mutual exclusion
- CollectionRunner, CycleSummary: Scheduled collection cycle execution and metrics
- create_scheduler: APScheduler factory function
"""

from typing import Any

from backend.scheduler.config import (
    SchedulerConfig,
    get_scheduler_config,
    DEFAULT_SOURCES,
    parse_source_list,
)
from backend.scheduler.lock import SchedulerLock
from backend.scheduler.runner import CollectionRunner, CycleSummary


def create_scheduler(*args: Any, **kwargs: Any) -> Any:
    """Lazily load and create the APScheduler BlockingScheduler instance."""
    from backend.scheduler.scheduler import create_scheduler as _create_scheduler

    return _create_scheduler(*args, **kwargs)


__all__ = [
    "SchedulerConfig",
    "get_scheduler_config",
    "DEFAULT_SOURCES",
    "parse_source_list",
    "SchedulerLock",
    "CollectionRunner",
    "CycleSummary",
    "create_scheduler",
]
