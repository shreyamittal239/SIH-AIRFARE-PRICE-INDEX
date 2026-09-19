"""Execution runner for scheduled collection cycles.

Coordinates:
- Observation date derivation in the configured timezone (Asia/Kolkata)
- Loading active routes & booking windows from PostgreSQL
- Building the task matrix (N routes × M windows)
- Process-level mutual exclusion via SchedulerLock (PostgreSQL advisory locks)
- Configured maximum runtime watchdog (default 180 min)
- Sequential collector execution through CollectionOrchestrator
- Clean resource disposal (browser, contexts, DB connections, locks)
- Dry-run simulation mode (zero browser launch, zero DB mutations)
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.app.db.database import SessionLocal, engine
from backend.collectors.orchestrator import (
    CollectionOrchestrator,
    CollectionTask,
    CollectionStatus,
    TaskExecutionResult,
)
from backend.collectors.playwright.browser import BrowserManager
from backend.scheduler.config import SchedulerConfig, get_scheduler_config
from backend.scheduler.lock import SchedulerLock

logger = logging.getLogger("scheduler.runner")


@dataclass
class CycleSummary:
    """Detailed summary of a collection cycle."""

    status: str  # COMPLETED, FAILED, TIMED_OUT, SKIPPED_LOCKED, DRY_RUN
    observation_date: date
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    zero_inventory_tasks: int = 0
    quotes_extracted: int = 0
    observations_persisted: int = 0
    duration_seconds: float = 0.0
    sources: List[str] = field(default_factory=list)
    source_summaries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    error: Optional[str] = None


class CollectionRunner:
    """Executes a single scheduled or manual collection cycle."""

    def __init__(self, config: Optional[SchedulerConfig] = None) -> None:
        """Initialize runner with scheduler configuration."""
        self.config = config or get_scheduler_config()

    def get_observation_date(self) -> date:
        """Derive observation date consistently using configured project timezone."""
        return datetime.now(self.config.timezone).date()

    def run_cycle(
        self,
        dry_run: bool = False,
        route_limit: Optional[int] = None,
        window_codes: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
        observation_date_override: Optional[date] = None,
    ) -> CycleSummary:
        """Execute one complete collection cycle.

        Args:
            dry_run: If True, simulates execution and prints plan without launching browser or persisting data.
            route_limit: Optional limit on routes (useful for smoke tests during development).
            window_codes: Optional list of window codes to filter (e.g. ['T+1']).
            sources: Optional source list override (defaults to configured sources).
            observation_date_override: Optional observation date (defaults to current date in project timezone).

        Returns:
            CycleSummary object containing all metrics and task results.
        """
        obs_date = observation_date_override or self.get_observation_date()
        target_sources = sources or self.config.sources

        session = SessionLocal()
        try:
            orchestrator = CollectionOrchestrator(
                db=session,
                timezone_override=self.config.timezone,
            )

            # 1. Load active basket routes & booking windows
            routes = orchestrator.get_active_basket_routes(base_period_code=self.config.base_period_code)
            if route_limit is not None and route_limit > 0:
                routes = routes[:route_limit]

            windows = orchestrator.get_active_booking_windows()
            if window_codes:
                upper_codes = {w.strip().upper() for w in window_codes}
                windows = [w for w in windows if w.window_code.upper() in upper_codes]

            # 2. Build task matrix
            tasks_per_source = orchestrator.build_task_matrix(
                routes=routes,
                windows=windows,
                observation_date=obs_date,
            )
            total_tasks_count = len(tasks_per_source) * len(target_sources)

            # 3. Handle DRY-RUN Mode
            if dry_run:
                return self._handle_dry_run(
                    obs_date=obs_date,
                    routes=routes,
                    windows=windows,
                    tasks_per_source=tasks_per_source,
                    target_sources=target_sources,
                )

        finally:
            session.close()

        # 4. Acquire Process-Level Lock (PostgreSQL Advisory Lock)
        lock = SchedulerLock(lock_id=self.config.advisory_lock_id, engine=engine)
        if not lock.acquire():
            # Another cycle is currently active
            return CycleSummary(
                status="SKIPPED_LOCKED",
                observation_date=obs_date,
                total_tasks=total_tasks_count,
                sources=target_sources,
                error="Collection cycle skipped because another cycle is already running.",
            )

        cycle_start_time = time.time()
        deadline = cycle_start_time + (self.config.max_runtime_minutes * 60.0)
        timed_out = False

        summary = CycleSummary(
            status="COMPLETED",
            observation_date=obs_date,
            total_tasks=total_tasks_count,
            sources=target_sources,
        )

        logger.info("[SCHEDULER] Collection cycle started")
        logger.info("[SCHEDULER] Observation date: %s", obs_date)
        logger.info("[SCHEDULER] Routes: %d", len(routes))
        logger.info("[SCHEDULER] Windows: %d", len(windows))
        logger.info("[SCHEDULER] Sources: %d (%s)", len(target_sources), ", ".join(target_sources))
        logger.info("[SCHEDULER] Total tasks: %d", total_tasks_count)
        logger.info("[SCHEDULER] Maximum runtime allowed: %d minutes", self.config.max_runtime_minutes)

        try:
            # Reopen session for actual task execution
            exec_session = SessionLocal()
            try:
                exec_orchestrator = CollectionOrchestrator(
                    db=exec_session,
                    timezone_override=self.config.timezone,
                )

                for src_idx, source_code in enumerate(target_sources, start=1):
                    if timed_out:
                        break

                    logger.info("\n" + "=" * 70)
                    logger.info(
                        "[SCHEDULER] Starting Collector [%d/%d]: %s",
                        src_idx,
                        len(target_sources),
                        source_code,
                    )
                    logger.info("=" * 70)

                    src_summary = {
                        "total_tasks": len(tasks_per_source),
                        "completed": 0,
                        "failed": 0,
                        "zero_inventory": 0,
                        "quotes": 0,
                        "inserted": 0,
                    }

                    # Isolated BrowserManager per collector batch to prevent memory accumulation
                    bm = BrowserManager(headless=self.config.headless)
                    try:
                        for t_idx, task in enumerate(tasks_per_source, start=1):
                            # Check runtime limit watchdog
                            if time.time() >= deadline:
                                logger.warning(
                                    "[SCHEDULER] Maximum runtime limit (%d minutes) reached! Halting new tasks.",
                                    self.config.max_runtime_minutes,
                                )
                                timed_out = True
                                summary.status = "TIMED_OUT"
                                break

                            logger.info(
                                ">>> [Source: %s | Task %d/%d] Route: %s | Window: %s | Date: %s <<<",
                                source_code,
                                t_idx,
                                len(tasks_per_source),
                                task.route.route_code,
                                task.window.window_code,
                                task.travel_date,
                            )

                            t_start = time.time()
                            res: TaskExecutionResult = exec_orchestrator.execute_single_task(
                                task=task,
                                source_code=source_code,
                                browser_manager=bm,
                            )
                            t_dur = time.time() - t_start

                            # Clean up any browser context left behind
                            if hasattr(bm, "_contexts"):
                                for ctx in list(bm._contexts):
                                    try:
                                        bm.close_context(ctx)
                                    except Exception:
                                        pass

                            # Aggregate metrics
                            summary.quotes_extracted += res.quotes_count
                            src_summary["quotes"] += res.quotes_count

                            if res.status == "COMPLETED":
                                summary.completed_tasks += 1
                                summary.observations_persisted += res.inserted_count
                                src_summary["completed"] += 1
                                src_summary["inserted"] += res.inserted_count
                                if res.collection_status == CollectionStatus.SUCCESS_NO_INVENTORY:
                                    summary.zero_inventory_tasks += 1
                                    src_summary["zero_inventory"] += 1
                            else:
                                summary.failed_tasks += 1
                                src_summary["failed"] += 1

                            logger.info(
                                "<<< [%s #%d] Status=%s, Quotes=%d, Inserted=%d, RunId=%s, Duration=%.2fs >>>",
                                source_code,
                                t_idx,
                                res.status,
                                res.quotes_count,
                                res.inserted_count,
                                res.run_id,
                                t_dur,
                            )

                            # Polite pause between tasks
                            if t_idx < len(tasks_per_source) and self.config.task_delay_seconds > 0:
                                time.sleep(self.config.task_delay_seconds)

                    finally:
                        try:
                            bm.close()
                        except Exception as bm_err:
                            logger.warning("Notice while closing browser for %s: %s", source_code, bm_err)

                    summary.source_summaries[source_code] = src_summary
                    logger.info(
                        "[SCHEDULER] Collector %s finished: %d completed, %d failed, %d zero-inv, %d persisted",
                        source_code,
                        src_summary["completed"],
                        src_summary["failed"],
                        src_summary["zero_inventory"],
                        src_summary["inserted"],
                    )

            finally:
                exec_session.close()

        except Exception as exc:
            logger.error("[SCHEDULER] Unhandled exception during collection cycle: %s", exc, exc_info=True)
            summary.status = "FAILED"
            summary.error = str(exc)

        finally:
            cycle_duration = time.time() - cycle_start_time
            summary.duration_seconds = round(cycle_duration, 2)
            lock.release()

            logger.info("\n" + "=" * 70)
            logger.info("[SCHEDULER] Collection cycle %s", summary.status)
            logger.info("[SCHEDULER] Duration: %.2fs (%.2f min)", cycle_duration, cycle_duration / 60.0)
            logger.info("[SCHEDULER] Tasks completed: %d / %d", summary.completed_tasks, summary.total_tasks)
            logger.info("[SCHEDULER] Tasks failed: %d", summary.failed_tasks)
            logger.info("[SCHEDULER] Zero-inventory tasks: %d", summary.zero_inventory_tasks)
            logger.info("[SCHEDULER] Quotes extracted: %d", summary.quotes_extracted)
            logger.info("[SCHEDULER] Observations persisted: %d", summary.observations_persisted)
            logger.info("=" * 70)

        return summary

    def _handle_dry_run(
        self,
        obs_date: date,
        routes: List[Any],
        windows: List[Any],
        tasks_per_source: List[Any],
        target_sources: List[str],
    ) -> CycleSummary:
        """Handle dry-run output without launching browser or mutating database."""
        total_tasks = len(tasks_per_source) * len(target_sources)

        source_display_names = {
            "YATRA": "Yatra",
            "EASEMYTRIP": "EaseMyTrip",
            "SPICEJET": "SpiceJet",
            "AIR_INDIA_EXPRESS": "Air India Express",
            "CLEARTRIP": "Cleartrip",
        }

        print("\n" + "=" * 50)
        print("          COLLECTION DRY-RUN PLAN")
        print("=" * 50)
        print(f"Timezone: {self.config.timezone_name}")
        print(f"Schedule: {self.config.schedule_hour:02d}:{self.config.schedule_minute:02d} daily")
        print(f"Observation Date: {obs_date}")
        print(f"Routes: {len(routes)}")
        print(f"Booking windows: {len(windows)}")
        print(f"Tasks per source: {len(tasks_per_source)}")
        print("Configured sources:")
        for s in target_sources:
            disp = source_display_names.get(s.upper(), s)
            print(f"  {disp}")
        print(f"\nTotal source tasks: {total_tasks}")
        print("\nNo scraping performed because --dry-run was specified.")
        print("=" * 50 + "\n")

        logger.info("Dry-run plan generated successfully (%d tasks total across %d sources).", total_tasks, len(target_sources))

        return CycleSummary(
            status="DRY_RUN",
            observation_date=obs_date,
            total_tasks=total_tasks,
            sources=target_sources,
        )
