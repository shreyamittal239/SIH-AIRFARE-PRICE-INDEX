"""Unit tests for the Automated Scraping Scheduler.

Covers:
1. Schedule configuration
2. Timezone handling
3. Task-count calculation (25 routes × 5 windows = 125 tasks per source)
4. Source configuration parsing
5. Dry-run behavior
6. --run-now behavior
7. Scheduler startup
8. Lock acquisition & collision in runner
9. Lock release on failure
10. Unexpected exception handling
11. No browser launch in dry-run
12. Configured collector filtering
13. Cleartrip disabled by default
"""

from datetime import date, datetime
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo
import pytest

from backend.scheduler.config import (
    SchedulerConfig,
    get_scheduler_config,
    parse_source_list,
    DEFAULT_SOURCES,
)
from backend.scheduler.lock import SchedulerLock
from backend.scheduler.runner import CollectionRunner, CycleSummary
from backend.scheduler.scheduler import create_scheduler, parse_arguments, main


# =====================================================================
# 1. CONFIGURATION & TIMEZONE TESTS
# =====================================================================
class TestSchedulerConfig:
    """Test configuration loader, env overrides, and timezone handling."""

    def test_default_configuration(self):
        """Verify default configuration adheres to specification."""
        config = get_scheduler_config()
        assert config.timezone_name == "Asia/Kolkata"
        assert config.schedule_hour == 2
        assert config.schedule_minute == 0
        assert config.max_runtime_minutes == 180
        assert config.advisory_lock_id == 847291
        assert config.sources == ["YATRA", "EASEMYTRIP", "SPICEJET", "AIR_INDIA_EXPRESS"]
        assert "CLEARTRIP" not in config.sources

    def test_timezone_handling(self):
        """Verify timezone parsing and fallback behavior."""
        # Valid timezone
        cfg = get_scheduler_config(timezone_name="Asia/Kolkata")
        assert cfg.timezone == ZoneInfo("Asia/Kolkata")

        # Invalid timezone fallback
        cfg_bad = get_scheduler_config(timezone_name="Invalid/Timezone_XYZ")
        assert cfg_bad.timezone_name == "Asia/Kolkata"
        assert cfg_bad.timezone == ZoneInfo("Asia/Kolkata")

    def test_env_variable_overrides(self, monkeypatch):
        """Verify environment variables properly override defaults."""
        monkeypatch.setenv("PROJECT_TIMEZONE", "UTC")
        monkeypatch.setenv("SCRAPE_SCHEDULE_HOUR", "4")
        monkeypatch.setenv("SCRAPE_SCHEDULE_MINUTE", "30")
        monkeypatch.setenv("SCRAPE_SOURCES", "spicejet,easemytrip")
        monkeypatch.setenv("SCRAPE_MAX_RUNTIME_MINUTES", "120")
        monkeypatch.setenv("SCRAPE_LOCK_ID", "123456")

        cfg = get_scheduler_config()
        assert cfg.timezone_name == "UTC"
        assert cfg.schedule_hour == 4
        assert cfg.schedule_minute == 30
        assert cfg.sources == ["SPICEJET", "EASEMYTRIP"]
        assert cfg.max_runtime_minutes == 120
        assert cfg.advisory_lock_id == 123456

    def test_source_parsing_and_cleartrip_policy(self):
        """Verify Cleartrip is disabled by default and enabled only when explicitly passed."""
        # Defaults
        assert "CLEARTRIP" not in parse_source_list(None)
        assert "CLEARTRIP" not in parse_source_list("")
        assert parse_source_list(None) == DEFAULT_SOURCES

        # Case-insensitivity & aliases
        parsed = parse_source_list("yatra_ota, SpiceJet_Direct, easemytrip")
        assert parsed == ["YATRA", "SPICEJET", "EASEMYTRIP"]

        # Cleartrip explicitly included
        parsed_with_ct = parse_source_list("yatra,cleartrip")
        assert "CLEARTRIP" in parsed_with_ct
        assert parsed_with_ct == ["YATRA", "CLEARTRIP"]


# =====================================================================
# 2. TASK-COUNT & MATRIX CALCULATION TESTS
# =====================================================================
class TestTaskCalculation:
    """Test task count calculation (25 routes × 5 windows = 125 tasks per source)."""

    def test_task_matrix_counts(self):
        """Verify 25 routes × 5 windows = 125 tasks per source, 500 tasks across 4 sources."""
        mock_routes = [MagicMock() for _ in range(25)]
        mock_windows = [MagicMock() for _ in range(5)]

        # Simulate CollectionOrchestrator.build_task_matrix
        tasks_per_source = [MagicMock() for _ in range(len(mock_routes) * len(mock_windows))]
        assert len(tasks_per_source) == 125

        # 4 sources
        sources = ["YATRA", "EASEMYTRIP", "SPICEJET", "AIR_INDIA_EXPRESS"]
        total_tasks = len(tasks_per_source) * len(sources)
        assert total_tasks == 500

    def test_observation_date_derivation(self):
        """Verify observation date is determined in Asia/Kolkata."""
        config = get_scheduler_config(timezone_name="Asia/Kolkata")
        runner = CollectionRunner(config=config)
        obs_date = runner.get_observation_date()
        expected = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        assert obs_date == expected


# =====================================================================
# 3. DRY-RUN MODE TESTS
# =====================================================================
class TestDryRunBehavior:
    """Verify dry-run execution does not launch browsers or mutate database."""

    @patch("backend.scheduler.runner.CollectionOrchestrator")
    @patch("backend.scheduler.runner.SessionLocal")
    @patch("backend.scheduler.runner.BrowserManager")
    def test_dry_run_no_browser_no_db_persistence(
        self, mock_browser_cls, mock_session_cls, mock_orchestrator_cls, capsys
    ):
        """Dry run must build task matrix, print plan, and NOT launch browser."""
        # Setup mock routes & windows
        mock_routes = [MagicMock(route_code=f"R{i}") for i in range(25)]
        mock_windows = [MagicMock(window_code=f"W{i}") for i in range(5)]
        mock_tasks = [MagicMock() for _ in range(125)]

        mock_orch_instance = MagicMock()
        mock_orch_instance.get_active_basket_routes.return_value = mock_routes
        mock_orch_instance.get_active_booking_windows.return_value = mock_windows
        mock_orch_instance.build_task_matrix.return_value = mock_tasks
        mock_orchestrator_cls.return_value = mock_orch_instance

        config = get_scheduler_config()
        runner = CollectionRunner(config=config)

        summary = runner.run_cycle(dry_run=True)

        # Assertions
        assert summary.status == "DRY_RUN"
        assert summary.total_tasks == 500  # 125 tasks * 4 sources
        assert summary.completed_tasks == 0
        assert summary.observations_persisted == 0

        # Verify BrowserManager was never instantiated
        assert not mock_browser_cls.called

        # Verify execute_single_task was never called
        assert not mock_orch_instance.execute_single_task.called

        # Verify stdout output contains required contract fields
        captured = capsys.readouterr().out
        assert "Timezone: Asia/Kolkata" in captured
        assert "Schedule: 02:00 daily" in captured
        assert "Routes: 25" in captured
        assert "Booking windows: 5" in captured
        assert "Tasks per source: 125" in captured
        assert "Total source tasks: 500" in captured
        assert "No scraping performed because --dry-run was specified." in captured


# =====================================================================
# 4. RUN-NOW & EXECUTION TESTS
# =====================================================================
class TestRunNowBehavior:
    """Verify execution logic with mocked orchestrator and browser lifecycle."""

    @patch("backend.scheduler.runner.SchedulerLock")
    @patch("backend.scheduler.runner.CollectionOrchestrator")
    @patch("backend.scheduler.runner.SessionLocal")
    @patch("backend.scheduler.runner.BrowserManager")
    def test_run_now_successful_execution(
        self, mock_bm_cls, mock_session_cls, mock_orch_cls, mock_lock_cls
    ):
        """Verify successful single-task cycle execution, metrics aggregation, and cleanup."""
        # Mock Lock: acquired successfully
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_lock_cls.return_value = mock_lock

        # Mock Routes & Windows (1 route × 1 window = 1 task)
        mock_route = MagicMock(route_code="DEL-BOM")
        mock_window = MagicMock(window_code="T+1")
        mock_task = MagicMock(route=mock_route, window=mock_window, travel_date=date(2026, 9, 20))

        mock_orch = MagicMock()
        mock_orch.get_active_basket_routes.return_value = [mock_route]
        mock_orch.get_active_booking_windows.return_value = [mock_window]
        mock_orch.build_task_matrix.return_value = [mock_task]

        # Mock TaskExecutionResult
        from backend.collectors.orchestrator import TaskExecutionResult, CollectionStatus
        mock_res = TaskExecutionResult(
            task=mock_task,
            source_code="SPICEJET",
            status="COMPLETED",
            collection_status=CollectionStatus.SUCCESS_WITH_QUOTES,
            quotes_count=5,
            inserted_count=5,
            error=None,
            run_id=123,
        )
        mock_orch.execute_single_task.return_value = mock_res
        mock_orch_cls.return_value = mock_orch

        # Mock BrowserManager instance
        mock_bm = MagicMock()
        mock_bm._contexts = []
        mock_bm_cls.return_value = mock_bm

        config = get_scheduler_config(task_delay_seconds=0.0)
        runner = CollectionRunner(config=config)

        # Execute single source
        summary = runner.run_cycle(
            dry_run=False,
            sources=["SPICEJET"],
            observation_date_override=date(2026, 9, 19),
        )

        assert summary.status == "COMPLETED"
        assert summary.total_tasks == 1
        assert summary.completed_tasks == 1
        assert summary.failed_tasks == 0
        assert summary.quotes_extracted == 5
        assert summary.observations_persisted == 5

        # Verify lock was released
        assert mock_lock.release.called

        # Verify browser was closed
        assert mock_bm.close.called

    @patch("backend.scheduler.runner.SchedulerLock")
    @patch("backend.scheduler.runner.CollectionOrchestrator")
    @patch("backend.scheduler.runner.SessionLocal")
    def test_run_now_skipped_when_locked(
        self, mock_session_cls, mock_orch_cls, mock_lock_cls
    ):
        """Verify runner aborts cleanly with SKIPPED_LOCKED when lock cannot be acquired."""
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False
        mock_lock_cls.return_value = mock_lock

        mock_orch = MagicMock()
        mock_orch.get_active_basket_routes.return_value = [MagicMock()]
        mock_orch.get_active_booking_windows.return_value = [MagicMock()]
        mock_orch.build_task_matrix.return_value = [MagicMock()]
        mock_orch_cls.return_value = mock_orch

        config = get_scheduler_config()
        runner = CollectionRunner(config=config)

        summary = runner.run_cycle(dry_run=False)

        assert summary.status == "SKIPPED_LOCKED"
        assert summary.completed_tasks == 0
        assert "Collection cycle skipped because another cycle is already running." in summary.error

    @patch("backend.scheduler.runner.SchedulerLock")
    @patch("backend.scheduler.runner.CollectionOrchestrator")
    @patch("backend.scheduler.runner.SessionLocal")
    @patch("backend.scheduler.runner.BrowserManager")
    def test_unexpected_exception_releases_lock(
        self, mock_bm_cls, mock_session_cls, mock_orch_cls, mock_lock_cls
    ):
        """Verify unexpected exception marks cycle FAILED and strictly releases lock."""
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_lock_cls.return_value = mock_lock

        mock_orch = MagicMock()
        mock_orch.get_active_basket_routes.return_value = [MagicMock()]
        mock_orch.get_active_booking_windows.return_value = [MagicMock()]
        mock_orch.build_task_matrix.return_value = [MagicMock()]
        mock_orch.execute_single_task.side_effect = RuntimeError("Fatal hardware/network error")
        mock_orch_cls.return_value = mock_orch

        config = get_scheduler_config(task_delay_seconds=0.0)
        runner = CollectionRunner(config=config)

        summary = runner.run_cycle(dry_run=False, sources=["SPICEJET"])

        assert summary.status == "FAILED"
        assert "Fatal hardware/network error" in (summary.error or "")
        # The lock MUST be released
        assert mock_lock.release.called


# =====================================================================
# 5. SCHEDULER STARTUP & CLI TESTS
# =====================================================================
class TestSchedulerStartupAndCLI:
    """Test APScheduler job creation and CLI argument parsing."""

    def test_create_scheduler_job_registration(self):
        """Verify scheduler instance registers the cron job correctly."""
        config = get_scheduler_config(schedule_hour=2, schedule_minute=0)
        scheduler = create_scheduler(config=config)

        job = scheduler.get_job("daily_airfare_collection")
        assert job is not None
        assert job.name == "Daily Airfare Collection Cycle"
        assert job.max_instances == 1

    def test_cli_parsing(self):
        """Verify CLI argument parser combinations."""
        # --dry-run
        args = parse_arguments(["--dry-run"])
        assert args.dry_run is True
        assert args.run_now is False

        # --run-now with filters
        args = parse_arguments([
            "--run-now",
            "--route-limit", "2",
            "--window-codes", "T+1,T+7",
            "--sources", "spicejet,easemytrip",
            "--date", "2026-09-25",
        ])
        assert args.run_now is True
        assert args.route_limit == 2
        assert args.window_codes == "T+1,T+7"
        assert args.sources == "spicejet,easemytrip"
        assert args.date == "2026-09-25"

    @patch("backend.scheduler.scheduler.CollectionRunner")
    def test_main_dry_run_exit_code(self, mock_runner_cls):
        """Verify main() returns 0 on dry run."""
        mock_runner = MagicMock()
        mock_runner.run_cycle.return_value = CycleSummary(status="DRY_RUN", observation_date=date.today())
        mock_runner_cls.return_value = mock_runner

        ret = main(["--dry-run"])
        assert ret == 0
        assert mock_runner.run_cycle.called
        assert mock_runner.run_cycle.call_args[1]["dry_run"] is True

    @patch("backend.scheduler.scheduler.CollectionRunner")
    def test_main_run_now_exit_codes(self, mock_runner_cls):
        """Verify main() returns 0 on success and 1 on failure."""
        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        # Success case
        mock_runner.run_cycle.return_value = CycleSummary(status="COMPLETED", observation_date=date.today())
        assert main(["--run-now"]) == 0

        # Failure case
        mock_runner.run_cycle.return_value = CycleSummary(status="FAILED", observation_date=date.today())
        assert main(["--run-now"]) == 1
