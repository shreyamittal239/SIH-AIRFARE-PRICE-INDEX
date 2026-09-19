"""Database-driven collection orchestrator and collector adapter registry.

Orchestrates the execution of airline direct and OTA fare collectors across
dynamically queried route baskets (from route_weights) and booking windows (from booking_windows).

Guarantees:
- Fully database-driven: N active routes × M active booking windows = total tasks per source.
  Supports future route additions (e.g. DEL-JAI) and window additions with zero code changes.
- Explicit CollectionStatus & CollectionResult:
  - SUCCESS_WITH_QUOTES: Quotes parsed and persisted.
  - SUCCESS_NO_INVENTORY: Confirmed zero-inventory banner rendered (COMPLETED with records_scraped=0).
  - FAILED: Technical exception, WAF/network failure, or unexpected empty result (broken selector).
- Task-level error isolation: Failure on one route/window/source does NOT terminate the batch.
- Canonical route validation: Enforces origin != dest, 3-letter IATA format, and exact advance date math.
- Timezone awareness: Defaults observation date to Indian Standard Time (Asia/Kolkata, UTC+05:30)
  matching Indian domestic travel booking operations, with configurable overrides.
- Standardized CollectorAdapter: Wraps Yatra, EaseMyTrip, Cleartrip, Air India Express, and SpiceJet.
- Transactional persistence via existing IngestionRepository and product fingerprint deduplication.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Type, Union
from zoneinfo import ZoneInfo

# pyrefly: ignore [missing-import]
from sqlalchemy import select, and_
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

from backend.app.db.database import SessionLocal
from backend.app.db.models.route_weight import RouteWeight
from backend.app.db.models.route import Route
from backend.app.db.models.city import City
from backend.app.db.models.booking_window import BookingWindow
from backend.app.db.models.data_source import DataSource
from backend.app.db.models.collection_run import CollectionRun
from backend.collectors.playwright.browser import BrowserManager
from backend.collectors.playwright.schemas.flight_quote import FlightQuote
from backend.processing.ingestion.repository import IngestionRepository, IngestionResult

logger = logging.getLogger(__name__)

# IATA 3-letter uppercase code regex
IATA_REGEX = re.compile(r"^[A-Z]{3}$")

# Project timezone for Indian domestic aviation operations (configurable via env)
PROJECT_TIMEZONE_NAME = os.getenv("PROJECT_TIMEZONE", "Asia/Kolkata")
try:
    PROJECT_TIMEZONE = ZoneInfo(PROJECT_TIMEZONE_NAME)
except Exception:
    PROJECT_TIMEZONE = timezone.utc

# Known phrases indicating genuine absence of flight inventory on portal pages
NO_INVENTORY_KEYWORDS = [
    "no flights found",
    "sorry, no flights",
    "no flights available",
    "no available flights",
    "try the closest available date",
    "we couldn't find any flights",
    "we could not find any flights",
    "no direct flights available",
    "no direct flights found",
    "no results found",
    "no inventory available",
]


def get_current_observation_date(tz: Optional[Union[ZoneInfo, timezone]] = None) -> date:
    """Get current calendar date in project timezone (default Asia/Kolkata)."""
    target_tz = tz or PROJECT_TIMEZONE
    return datetime.now(target_tz).date()


def detect_no_inventory_banner(content: Any) -> Tuple[bool, str]:
    """Check if page content or exception string contains confirmed zero-inventory indicators."""
    if not content:
        return False, ""
    text_lower = str(content).lower()
    for kw in NO_INVENTORY_KEYWORDS:
        if kw in text_lower:
            return True, f"Confirmed zero-inventory banner detected: '{kw}'"
    return False, ""


class TaskValidationError(Exception):
    """Raised when a collection task fails validation."""
    pass


class CollectionStatus(str, Enum):
    """Explicit outcome status for a collection task."""

    SUCCESS_WITH_QUOTES = "SUCCESS_WITH_QUOTES"
    SUCCESS_NO_INVENTORY = "SUCCESS_NO_INVENTORY"
    FAILED = "FAILED"


@dataclass
class CollectionResult:
    """Standardized result returned by all CollectorAdapters."""

    status: CollectionStatus
    quotes: List[FlightQuote] = field(default_factory=list)
    error_message: Optional[str] = None
    no_inventory_reason: Optional[str] = None


@dataclass(frozen=True)
class BasketRoute:
    """Represents an active route in the route basket."""

    route_id: int
    route_code: str
    origin_code: str
    destination_code: str
    weight: Decimal
    passenger_volume: int


@dataclass(frozen=True)
class WindowInfo:
    """Represents an active booking window."""

    window_id: int
    window_code: str
    target_advance_days: int
    display_order: int


@dataclass(frozen=True)
class CollectionTask:
    """A discrete collection unit: a specific route × booking window on a given date."""

    route: BasketRoute
    window: WindowInfo
    travel_date: date
    observation_date: date


@dataclass
class TaskExecutionResult:
    """Outcome of a single collection task."""

    task: CollectionTask
    source_code: str
    run_id: Optional[int] = None
    status: str = "PENDING"  # COMPLETED or FAILED
    collection_status: Optional[CollectionStatus] = None
    quotes_count: int = 0
    inserted_count: int = 0
    skipped_count: int = 0
    error: Optional[str] = None
    attempts: int = 1
    retried: bool = False
    recovered_on_retry: bool = False


@dataclass
class BatchExecutionSummary:
    """Aggregated outcome of a batch collection run."""

    source_code: str
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_inserted: int = 0
    total_skipped: int = 0
    zero_quote_tasks: int = 0
    retried_tasks: int = 0
    retry_recovered_tasks: int = 0
    results: List[TaskExecutionResult] = field(default_factory=list)


def is_transient_technical_error(error_msg: str) -> bool:
    """Determine if an error represents a retryable transient technical failure.

    Retries genuine technical issues (timeouts, network hiccups, page crashes,
    unexpected empty results from API stall), but NEVER retries confirmed zero-inventory,
    business logic rejections, or validation failures.
    """
    if not error_msg:
        return False
    lower = error_msg.lower()
    retryable_keywords = [
        "timeout",
        "timeoutexceeded",
        "net::",
        "connection",
        "econnreset",
        "econnrefused",
        "target closed",
        "page crashed",
        "browser closed",
        "navigating to",
        "unexpected empty result",
        "interrupted",
        "protocol error",
    ]
    return any(kw in lower for kw in retryable_keywords)


def validate_task(task: CollectionTask) -> None:
    """Validate task parameters before handing off to a collector.

    Raises:
        TaskValidationError: If origin == dest, IATA codes are invalid, or dates are inconsistent.
    """
    orig = task.route.origin_code.strip().upper()
    dest = task.route.destination_code.strip().upper()

    if orig == dest:
        raise TaskValidationError(f"Invalid route: origin and destination are identical ({orig}).")

    if not IATA_REGEX.match(orig):
        raise TaskValidationError(f"Invalid origin IATA code: '{orig}'")

    if not IATA_REGEX.match(dest):
        raise TaskValidationError(f"Invalid destination IATA code: '{dest}'")

    expected_advance = task.window.target_advance_days
    actual_advance = (task.travel_date - task.observation_date).days
    if actual_advance != expected_advance:
        raise TaskValidationError(
            f"Date mismatch: travel_date {task.travel_date} is {actual_advance} days from "
            f"observation_date {task.observation_date}, but window {task.window.window_code} requires {expected_advance} days."
        )


# ==============================================================================
# COLLECTOR ADAPTER INTERFACE & IMPLEMENTATIONS
# ==============================================================================

class CollectorAdapter(ABC):
    """Abstract adapter ensuring all collectors adhere to a uniform execution interface."""

    @abstractmethod
    def collect(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        browser_manager: Optional[BrowserManager] = None,
    ) -> CollectionResult:
        """Execute search for the given route and date, returning CollectionResult."""
        pass


class YatraCollectorAdapter(CollectorAdapter):
    """Adapter for Yatra OTA collector."""

    source_identifier: str = "Yatra"

    def collect(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        browser_manager: Optional[BrowserManager] = None,
    ) -> CollectionResult:
        from backend.collectors.playwright.yatra_collector import YatraCollector
        collector = YatraCollector(browser_manager=browser_manager)
        try:
            quotes = collector.collect(
                origin=origin,
                destination=destination,
                travel_date=travel_date,
            )
            if quotes:
                return CollectionResult(status=CollectionStatus.SUCCESS_WITH_QUOTES, quotes=quotes)

            # Distinguish genuine zero inventory vs parser breakdown
            page_text = collector.page.locator("body").inner_text() if collector.page else ""
            is_no_inv, reason = detect_no_inventory_banner(page_text)
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)

            return CollectionResult(
                status=CollectionStatus.FAILED,
                error_message="Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.",
            )
        except Exception as exc:
            is_no_inv, reason = detect_no_inventory_banner(str(exc))
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)
            return CollectionResult(status=CollectionStatus.FAILED, error_message=str(exc))


class EaseMyTripCollectorAdapter(CollectorAdapter):
    """Adapter for EaseMyTrip OTA collector."""

    source_identifier: str = "easemytrip_ota"

    def collect(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        browser_manager: Optional[BrowserManager] = None,
    ) -> CollectionResult:
        from backend.collectors.playwright.easemytrip_collector import EaseMyTripCollector
        collector = EaseMyTripCollector(browser_manager=browser_manager)
        try:
            quotes = collector.collect(
                origin=origin,
                destination=destination,
                travel_date=travel_date,
            )
            if quotes:
                return CollectionResult(status=CollectionStatus.SUCCESS_WITH_QUOTES, quotes=quotes)

            page_text = getattr(collector, "_last_page_text", "")
            if not page_text and collector.page:
                try:
                    page_text = collector.page.locator("body").inner_text()
                except Exception:
                    page_text = ""

            is_no_inv, reason = detect_no_inventory_banner(page_text)
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)

            return CollectionResult(
                status=CollectionStatus.FAILED,
                error_message="Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.",
            )
        except Exception as exc:
            # EaseMyTrip raises CollectorDateMismatchError with 'No inventory available'
            is_no_inv, reason = detect_no_inventory_banner(str(exc))
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)
            return CollectionResult(status=CollectionStatus.FAILED, error_message=str(exc))
        finally:
            collector.close()


class CleartripCollectorAdapter(CollectorAdapter):
    """Adapter for Cleartrip OTA collector."""

    source_identifier: str = "cleartrip_ota"

    def collect(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        browser_manager: Optional[BrowserManager] = None,
    ) -> CollectionResult:
        from backend.collectors.playwright.cleartrip_collector import CleartripCollector
        collector = CleartripCollector(browser_manager=browser_manager)
        try:
            quotes = collector.collect(
                origin=origin,
                destination=destination,
                travel_date=travel_date,
            )
            if quotes:
                return CollectionResult(status=CollectionStatus.SUCCESS_WITH_QUOTES, quotes=quotes)

            # Check intercepted API response for confirmed empty card array
            api_cards = (collector._captured_api_payload or {}).get("cards", {}).get("J1", None)
            if api_cards == []:
                return CollectionResult(
                    status=CollectionStatus.SUCCESS_NO_INVENTORY,
                    no_inventory_reason="Cleartrip API confirmed zero flight cards for route.",
                )

            page_text = getattr(collector, "_last_page_text", "")
            if not page_text and collector.page:
                try:
                    page_text = collector.page.locator("body").inner_text()
                except Exception:
                    page_text = ""

            is_no_inv, reason = detect_no_inventory_banner(page_text)
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)

            return CollectionResult(
                status=CollectionStatus.FAILED,
                error_message="Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.",
            )
        except Exception as exc:
            is_no_inv, reason = detect_no_inventory_banner(str(exc))
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)
            return CollectionResult(status=CollectionStatus.FAILED, error_message=str(exc))
        finally:
            collector.close()


class AirIndiaExpressCollectorAdapter(CollectorAdapter):
    """Adapter for Air India Express Direct collector."""

    source_identifier: str = "Air India Express Direct"

    def collect(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        browser_manager: Optional[BrowserManager] = None,
    ) -> CollectionResult:
        from backend.collectors.playwright.air_india_express_collector import AirIndiaExpressCollector
        collector = AirIndiaExpressCollector(browser_manager=browser_manager)
        try:
            quotes = collector.collect(
                origin=origin,
                destination=destination,
                travel_date=travel_date,
            )
            if quotes:
                return CollectionResult(status=CollectionStatus.SUCCESS_WITH_QUOTES, quotes=quotes)

            page_text = collector.page.locator("body").inner_text() if collector.page else ""
            is_no_inv, reason = detect_no_inventory_banner(page_text)
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)

            return CollectionResult(
                status=CollectionStatus.FAILED,
                error_message="Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.",
            )
        except Exception as exc:
            is_no_inv, reason = detect_no_inventory_banner(str(exc))
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)
            return CollectionResult(status=CollectionStatus.FAILED, error_message=str(exc))


class SpiceJetCollectorAdapter(CollectorAdapter):
    """Adapter for SpiceJet Direct collector."""

    source_identifier: str = "SpiceJet Direct"

    def collect(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        browser_manager: Optional[BrowserManager] = None,
    ) -> CollectionResult:
        from backend.collectors.playwright.collectors.first_airline_collector import FirstAirlineCollector
        collector = FirstAirlineCollector(
            origin=origin,
            destination=destination,
            travel_date=travel_date,
            browser_manager=browser_manager,
        )
        try:
            quotes = collector.collect_quotes()
            if quotes:
                return CollectionResult(status=CollectionStatus.SUCCESS_WITH_QUOTES, quotes=quotes)

            # Explicit check on collector zero-inventory state
            if getattr(collector, "is_zero_inventory", False):
                return CollectionResult(
                    status=CollectionStatus.SUCCESS_NO_INVENTORY,
                    no_inventory_reason=getattr(
                        collector,
                        "no_inventory_reason",
                        "SpiceJet confirmed no flights available.",
                    ),
                )

            page_text = collector.page.locator("body").inner_text() if collector.page else ""
            is_no_inv, reason = detect_no_inventory_banner(page_text)
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)

            return CollectionResult(
                status=CollectionStatus.FAILED,
                error_message="Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.",
            )
        except Exception as exc:
            # If collector already confirmed zero-inventory, honor that outcome
            if getattr(collector, "is_zero_inventory", False):
                return CollectionResult(
                    status=CollectionStatus.SUCCESS_NO_INVENTORY,
                    no_inventory_reason=getattr(
                        collector,
                        "no_inventory_reason",
                        "SpiceJet confirmed no flights available.",
                    ),
                )

            exc_str = str(exc)
            # Safeguard: DNS or network failures MUST remain FAILED (never convert to zero inventory)
            if "ERR_NAME_NOT_RESOLVED" in exc_str or "net::" in exc_str or "DNS" in exc_str:
                return CollectionResult(status=CollectionStatus.FAILED, error_message=exc_str)

            is_no_inv, reason = detect_no_inventory_banner(exc_str)
            if is_no_inv:
                return CollectionResult(status=CollectionStatus.SUCCESS_NO_INVENTORY, no_inventory_reason=reason)
            return CollectionResult(status=CollectionStatus.FAILED, error_message=exc_str)
        finally:
            collector.close()



# Registry mapping source codes (and aliases) to CollectorAdapters
COLLECTOR_REGISTRY: Dict[str, CollectorAdapter] = {
    "YATRA": YatraCollectorAdapter(),
    "YATRA_OTA": YatraCollectorAdapter(),
    "EASEMYTRIP": EaseMyTripCollectorAdapter(),
    "EASEMYTRIP_OTA": EaseMyTripCollectorAdapter(),
    "CLEARTRIP": CleartripCollectorAdapter(),
    "CLEARTRIP_OTA": CleartripCollectorAdapter(),
    "AIR_INDIA_EXPRESS": AirIndiaExpressCollectorAdapter(),
    "AIR_INDIA_EXPRESS_DIRECT": AirIndiaExpressCollectorAdapter(),
    "SPICEJET": SpiceJetCollectorAdapter(),
    "SPICEJET_DIRECT": SpiceJetCollectorAdapter(),
}


def get_collector_adapter(source_code: str) -> CollectorAdapter:
    """Lookup registered CollectorAdapter for a given data source code."""
    canonical = source_code.strip().upper()
    if canonical not in COLLECTOR_REGISTRY:
        raise ValueError(
            f"No collector adapter registered for source code '{source_code}'. "
            f"Available: {sorted(list(COLLECTOR_REGISTRY.keys()))}"
        )
    return COLLECTOR_REGISTRY[canonical]


# ==============================================================================
# COLLECTION ORCHESTRATOR
# ==============================================================================

class CollectionOrchestrator:
    """Orchestrates database-driven fare collection across routes and booking windows."""

    def __init__(
        self,
        db: Session,
        timezone_override: Optional[Union[ZoneInfo, timezone]] = None,
    ) -> None:
        """Initialize orchestrator with an active database session."""
        self.db = db
        self.repository = IngestionRepository(db)
        self.timezone = timezone_override or PROJECT_TIMEZONE

    def get_active_basket_routes(
        self,
        base_period_code: str = "2026-07",
    ) -> List[BasketRoute]:
        """Dynamically query all active routes belonging to the specified basket period.

        Reads from route_weights joined with routes and cities.
        Supports any number of routes (N routes) with zero hardcoded route lists.

        Args:
            base_period_code: Target base period code (default: '2026-07').

        Returns:
            List of BasketRoute objects ordered by weight descending.
        """
        stmt = (
            select(RouteWeight)
            .join(Route, RouteWeight.route_id == Route.route_id)
            .where(
                RouteWeight.is_active == True,
                RouteWeight.base_period_code == base_period_code,
                Route.is_active == True,
            )
            .order_by(RouteWeight.weight.desc())
        )
        weights = self.db.scalars(stmt).all()

        basket_routes: List[BasketRoute] = []
        for w in weights:
            route = w.route
            basket_routes.append(
                BasketRoute(
                    route_id=route.route_id,
                    route_code=route.route_code,
                    origin_code=route.origin_city.city_code,
                    destination_code=route.destination_city.city_code,
                    weight=w.weight,
                    passenger_volume=w.passenger_volume,
                )
            )

        logger.info(
            "Loaded %d active basket routes for base_period_code='%s'",
            len(basket_routes),
            base_period_code,
        )
        return basket_routes

    def get_active_booking_windows(self) -> List[WindowInfo]:
        """Dynamically query all active booking windows from the database.

        Supports any number of windows (M windows) with zero hardcoded window lists.

        Returns:
            List of WindowInfo objects ordered by display_order ascending.
        """
        stmt = (
            select(BookingWindow)
            .where(BookingWindow.is_active == True)
            .order_by(BookingWindow.display_order)
        )
        windows = self.db.scalars(stmt).all()

        window_infos = [
            WindowInfo(
                window_id=w.window_id,
                window_code=w.window_code,
                target_advance_days=w.target_advance_days,
                display_order=w.display_order,
            )
            for w in windows
        ]
        logger.info("Loaded %d active booking windows from database", len(window_infos))
        return window_infos

    def build_task_matrix(
        self,
        routes: List[BasketRoute],
        windows: List[WindowInfo],
        observation_date: Optional[date] = None,
    ) -> List[CollectionTask]:
        """Construct the Cartesian product matrix: N routes × M booking windows.

        Computes exact travel date dynamically as:
        travel_date = observation_date + target_advance_days.

        Defaults observation date to project timezone (Asia/Kolkata) if not provided.

        Args:
            routes: List of active routes.
            windows: List of active booking windows.
            observation_date: Observation date (defaults to project timezone date).

        Returns:
            List of CollectionTask objects of size len(routes) * len(windows).
        """
        if observation_date is None:
            observation_date = get_current_observation_date(self.timezone)

        tasks: List[CollectionTask] = []
        for route in routes:
            for window in windows:
                travel_date = observation_date + timedelta(days=window.target_advance_days)
                task = CollectionTask(
                    route=route,
                    window=window,
                    travel_date=travel_date,
                    observation_date=observation_date,
                )
                tasks.append(task)

        logger.info(
            "Generated %d collection tasks (%d routes × %d windows) for observation_date=%s (tz=%s)",
            len(tasks),
            len(routes),
            len(windows),
            observation_date,
            getattr(self.timezone, "key", str(self.timezone)),
        )
        return tasks

    def execute_single_task(
        self,
        task: CollectionTask,
        source_code: str,
        browser_manager: Optional[BrowserManager] = None,
    ) -> TaskExecutionResult:
        """Execute a single collection task with complete error isolation.

        Handles:
        - Task validation
        - DataSource lookup
        - CollectionRun creation and auditing
        - Collector dispatch via CollectorAdapter
        - Distinguishing SUCCESS_WITH_QUOTES, SUCCESS_NO_INVENTORY, and FAILED
        - Transactional persistence of quotes into fare_observations
        """
        result = TaskExecutionResult(task=task, source_code=source_code)

        # 1. Validate task integrity
        try:
            validate_task(task)
        except TaskValidationError as val_err:
            logger.error("Task validation failed for %s on %s: %s", task.route.route_code, task.travel_date, val_err)
            result.status = "FAILED"
            result.error = str(val_err)
            return result

        # 2. Lookup CollectorAdapter
        try:
            adapter = get_collector_adapter(source_code)
        except ValueError as reg_err:
            logger.error("Registry lookup failed for '%s': %s", source_code, reg_err)
            result.status = "FAILED"
            result.error = str(reg_err)
            return result

        # 3. Resolve DataSource
        source_key = getattr(adapter, "source_identifier", None) or source_code
        try:
            data_source = self.repository.resolver.resolve_data_source(source_key)
        except Exception as src_err:
            logger.error("Failed resolving DataSource for '%s': %s", source_key, src_err)
            result.status = "FAILED"
            result.error = f"DataSource resolution failed: {src_err}"
            return result

        # 4. Create CollectionRun for this task: (source, target_route, target_window)
        run = self.repository.start_collection_run(
            source_id=data_source.source_id,
            target_route_id=task.route.route_id,
            target_window_id=task.window.window_id,
        )
        result.run_id = run.run_id

        # 5. Dispatch scraper with controlled technical retry (max 1 retry, 5s backoff)
        max_attempts = 2  # 1 initial attempt + 1 retry
        retry_delay_seconds = 5.0

        for attempt in range(1, max_attempts + 1):
            result.attempts = attempt
            if attempt > 1:
                result.retried = True
                logger.warning(
                    "Retrying task [Run %d, attempt %d/%d]: %s | Route %s | Window %s after %.1fs backoff...",
                    run.run_id,
                    attempt,
                    max_attempts,
                    source_code,
                    task.route.route_code,
                    task.window.window_code,
                    retry_delay_seconds,
                )
                time.sleep(retry_delay_seconds)

            try:
                res: CollectionResult = adapter.collect(
                    origin=task.route.origin_code,
                    destination=task.route.destination_code,
                    travel_date=task.travel_date,
                    browser_manager=browser_manager,
                )

                result.collection_status = res.status
                result.quotes_count = len(res.quotes)

                # 6. Handle Outcome Semantics
                if res.status == CollectionStatus.SUCCESS_WITH_QUOTES:
                    ingest_result: IngestionResult = self.repository.ingest_quotes(quotes=res.quotes, run=run)
                    result.inserted_count = ingest_result.inserted
                    result.skipped_count = ingest_result.skipped
                    result.status = "COMPLETED"
                    if attempt > 1:
                        result.recovered_on_retry = True
                        logger.info(
                            "Task recovered on retry [Run %d, attempt %d]: %d inserted, %d skipped",
                            run.run_id,
                            attempt,
                            result.inserted_count,
                            result.skipped_count,
                        )
                    else:
                        logger.info(
                            "Task completed with quotes [Run %d, attempt %d]: %d inserted, %d skipped",
                            run.run_id,
                            attempt,
                            result.inserted_count,
                            result.skipped_count,
                        )
                    return result

                elif res.status == CollectionStatus.SUCCESS_NO_INVENTORY:
                    # Confirmed no inventory on this route/date - DO NOT retry
                    logger.info(
                        "Search confirmed no inventory on %s (%s on %s): %s. Recording COMPLETED with records_scraped=0.",
                        source_code,
                        task.route.route_code,
                        task.travel_date,
                        res.no_inventory_reason,
                    )
                    self.repository.ingest_quotes(quotes=[], run=run)
                    result.status = "COMPLETED"
                    result.inserted_count = 0
                    return result

                else:  # CollectionStatus.FAILED
                    err_msg = res.error_message or "Collection failed"
                    if attempt < max_attempts and is_transient_technical_error(err_msg):
                        logger.warning(
                            "Technical failure on attempt %d for [Run %d] (%s on %s): %s. Preparing retry...",
                            attempt,
                            run.run_id,
                            task.route.route_code,
                            task.travel_date,
                            err_msg,
                        )
                        continue  # retry next attempt

                    logger.error(
                        "Task failed for %s (%s on %s) after %d attempt(s): %s",
                        source_code,
                        task.route.route_code,
                        task.travel_date,
                        attempt,
                        err_msg,
                    )
                    self.repository.complete_collection_run(run, status="FAILED", error_summary=err_msg)
                    result.status = "FAILED"
                    result.error = err_msg
                    return result

            except Exception as exc:
                err_msg = str(exc)
                if attempt < max_attempts and is_transient_technical_error(err_msg):
                    logger.warning(
                        "Transient exception on attempt %d for [Run %d] (%s on %s): %s. Preparing retry...",
                        attempt,
                        run.run_id,
                        task.route.route_code,
                        task.travel_date,
                        err_msg,
                    )
                    continue  # retry next attempt

                full_err = f"Unexpected collector execution error on {source_code} ({task.route.route_code}, {task.travel_date}): {exc}"
                logger.error(full_err, exc_info=True)
                self.repository.complete_collection_run(run, status="FAILED", error_summary=str(exc))
                result.status = "FAILED"
                result.error = str(exc)
                return result

    def run_collection(
        self,
        source_code: str,
        tasks: Optional[List[CollectionTask]] = None,
        base_period_code: str = "2026-07",
        observation_date: Optional[date] = None,
        route_limit: Optional[int] = None,
        window_codes: Optional[List[str]] = None,
        delay_seconds: float = 2.0,
        browser_manager: Optional[BrowserManager] = None,
    ) -> BatchExecutionSummary:
        """Run a batch collection across tasks with rate-limiting and failure isolation.

        Args:
            source_code: Canonical data source code (e.g. 'YATRA', 'CLEARTRIP', 'EASEMYTRIP').
            tasks: Optional pre-built task list. If None, generated from DB.
            base_period_code: Target basket base period code (default: '2026-07').
            observation_date: Target observation date (default: today in project timezone).
            route_limit: Optional integer to limit the number of routes (for smoke testing).
            window_codes: Optional list of window codes to filter (e.g. ['T+7']).
            delay_seconds: Pause between sequential tasks for rate limiting.
            browser_manager: Optional shared BrowserManager instance.

        Returns:
            BatchExecutionSummary detailing task outcomes.
        """
        if tasks is None:
            routes = self.get_active_basket_routes(base_period_code=base_period_code)
            if route_limit is not None and route_limit > 0:
                routes = routes[:route_limit]

            windows = self.get_active_booking_windows()
            if window_codes:
                upper_codes = {c.strip().upper() for c in window_codes}
                windows = [w for w in windows if w.window_code.upper() in upper_codes]

            tasks = self.build_task_matrix(routes, windows, observation_date=observation_date)

        summary = BatchExecutionSummary(
            source_code=source_code,
            total_tasks=len(tasks),
        )

        owns_browser = False
        if browser_manager is None:
            browser_manager = BrowserManager(headless=True)
            owns_browser = True

        try:
            for idx, task in enumerate(tasks, start=1):
                logger.info("Starting task %d of %d...", idx, len(tasks))
                res = self.execute_single_task(
                    task=task,
                    source_code=source_code,
                    browser_manager=browser_manager,
                )
                summary.results.append(res)

                if res.status == "COMPLETED":
                    summary.completed_tasks += 1
                    summary.total_inserted += res.inserted_count
                    summary.total_skipped += res.skipped_count
                    if res.quotes_count == 0:
                        summary.zero_quote_tasks += 1
                else:
                    summary.failed_tasks += 1

                if res.retried:
                    summary.retried_tasks += 1
                if res.recovered_on_retry:
                    summary.retry_recovered_tasks += 1

                # Polite delay between tasks
                if idx < len(tasks) and delay_seconds > 0:
                    time.sleep(delay_seconds)

        finally:
            if owns_browser:
                browser_manager.close()

        logger.info(
            "Batch collection completed for %s: %d total, %d completed, %d failed, %d inserted",
            source_code,
            summary.total_tasks,
            summary.completed_tasks,
            summary.failed_tasks,
            summary.total_inserted,
        )
        return summary
