"""Automated Scraping Scheduler CLI and Process Daemon.

SIH26056 — Real-Time Airfare Price Index for India.

This module is the entrypoint for the automated collection scheduler.
It can run in three modes:
1. Recurring daemon (default):
   python -m backend.scheduler.scheduler
   Schedules a daily scraping cycle using APScheduler at the configured time (default: 02:00 AM IST).

2. Manual one-shot (--run-now):
   python -m backend.scheduler.scheduler --run-now
   Executes a single collection cycle immediately and exits.

3. Dry run (--dry-run):
   python -m backend.scheduler.scheduler --dry-run
   Loads active routes & booking windows, builds the task matrix, and displays the plan
   without launching any browser, scraping, or mutating the database.

Optional development/testing filters:
   --route-limit N       Limit to the first N routes (for quick testing).
   --window-codes W1,W2  Limit to specific booking windows (e.g., T+1, T+7).
   --sources S1,S2       Override configured source list (e.g., spicejet).
   --date YYYY-MM-DD     Override observation date.
"""

import argparse
from datetime import date, datetime
import logging
import sys
from typing import List, Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.scheduler.config import SchedulerConfig, get_scheduler_config
from backend.scheduler.runner import CollectionRunner, CycleSummary

# Setup standard logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")


def create_scheduler(
    config: Optional[SchedulerConfig] = None,
    runner: Optional[CollectionRunner] = None,
) -> BlockingScheduler:
    """Create and configure the APScheduler BlockingScheduler instance."""
    cfg = config or get_scheduler_config()
    run_instance = runner or CollectionRunner(config=cfg)

    scheduler = BlockingScheduler(timezone=cfg.timezone)

    trigger = CronTrigger(
        hour=cfg.schedule_hour,
        minute=cfg.schedule_minute,
        timezone=cfg.timezone,
    )

    def scheduled_cycle_job() -> None:
        """Wrapper for scheduled execution with next run-time logging."""
        logger.info("[SCHEDULER] Scheduled collection trigger fired.")
        summary = run_instance.run_cycle()
        now_tz = datetime.now(cfg.timezone)
        next_fire = trigger.get_next_fire_time(None, now_tz)
        logger.info("[SCHEDULER] Next scheduled run: %s", next_fire)

    scheduler.add_job(
        func=scheduled_cycle_job,
        trigger=trigger,
        id="daily_airfare_collection",
        name="Daily Airfare Collection Cycle",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Automated Scraping Scheduler for SIH26056 Airfare Price Index."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--run-now",
        action="store_true",
        help="Run a single collection cycle immediately and exit.",
    )
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the task matrix and display plan without browser launch or DB persistence.",
    )

    # Optional development and testing filters
    parser.add_argument(
        "--route-limit",
        type=int,
        default=None,
        help="Limit execution to first N routes (useful for smoke tests).",
    )
    parser.add_argument(
        "--window-codes",
        type=str,
        default=None,
        help="Comma-separated booking window codes to filter (e.g. 'T+1,T+7').",
    )
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help="Comma-separated sources to run (e.g. 'spicejet,easemytrip'). Overrides env.",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Observation date override (YYYY-MM-DD format).",
    )

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Main scheduler entrypoint."""
    args = parse_arguments(argv)
    config = get_scheduler_config(sources_override=args.sources)
    runner = CollectionRunner(config=config)

    # Parse window codes if specified
    window_codes_list = None
    if args.window_codes:
        window_codes_list = [w.strip() for w in args.window_codes.split(",") if w.strip()]

    # Parse date override if specified
    obs_date_override: Optional[date] = None
    if args.date:
        try:
            obs_date_override = datetime.strptime(args.date.strip(), "%Y-%m-%d").date()
        except ValueError:
            logger.error("Invalid --date format '%s'. Must be YYYY-MM-DD.", args.date)
            return 2

    # Mode 1: Dry-Run Mode
    if args.dry_run:
        summary = runner.run_cycle(
            dry_run=True,
            route_limit=args.route_limit,
            window_codes=window_codes_list,
            sources=config.sources,
            observation_date_override=obs_date_override,
        )
        return 0

    # Mode 2: One-shot Manual Run (--run-now)
    if args.run_now:
        logger.info("[SCHEDULER] Manual one-shot trigger initiated (--run-now).")
        summary = runner.run_cycle(
            dry_run=False,
            route_limit=args.route_limit,
            window_codes=window_codes_list,
            sources=config.sources,
            observation_date_override=obs_date_override,
        )
        if summary.status in ("COMPLETED", "SKIPPED_LOCKED"):
            return 0
        return 1

    # Mode 3: Recurring Scheduler Daemon
    trigger = CronTrigger(
        hour=config.schedule_hour,
        minute=config.schedule_minute,
        timezone=config.timezone,
    )
    now_tz = datetime.now(config.timezone)
    next_run = trigger.get_next_fire_time(None, now_tz)

    logger.info("=" * 60)
    logger.info("[SCHEDULER] SCHEDULER STARTED")
    logger.info("[SCHEDULER] Timezone: %s", config.timezone_name)
    logger.info(
        "[SCHEDULER] Schedule: %02d:%02d daily",
        config.schedule_hour,
        config.schedule_minute,
    )
    logger.info("[SCHEDULER] Configured sources: %s", ", ".join(config.sources))
    logger.info("[SCHEDULER] Next scheduled run: %s", next_run)
    logger.info("[SCHEDULER] Press Ctrl+C to stop.")
    logger.info("=" * 60)

    scheduler = create_scheduler(config=config, runner=runner)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[SCHEDULER] Scheduler process interrupted by user. Shutting down cleanly...")
        scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] Scheduler stopped.")
        return 0
    except Exception as exc:
        logger.error("[SCHEDULER] Fatal error in scheduler daemon: %s", exc, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
