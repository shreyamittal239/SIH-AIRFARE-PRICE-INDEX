# Automated Scraping Scheduler — Implementation & Validation Report

**Project**: SIH26056 — Real-Time Airfare Price Index for India  
**Date**: September 17, 2026  
**Environment**: Windows Host, Python 3.13.15, PostgreSQL 16 (Docker), APScheduler 3.11.3  

---

## A. Existing Architecture Inspected

Before implementing the scheduler, the existing airfare collection pipeline was thoroughly inspected:
1. `backend/collectors/orchestrator.py`:
   - Contains `CollectionOrchestrator`, `BasketRoute`, `WindowInfo`, `CollectionTask`, and `TaskExecutionResult`.
   - `get_active_basket_routes(base_period_code='2026-07')` queries active routes dynamically from `route_weights` joined with `routes` and `cities`.
   - `get_active_booking_windows()` queries active windows ordered by `display_order`.
   - `build_task_matrix(...)` forms the Cartesian product of $N$ routes $\times$ $M$ windows for a given `observation_date`.
   - `execute_single_task(...)` handles individual task isolation, collection runs in `collection_runs`, collector dispatch via `CollectorAdapter`, and transactional persistence into `fare_observations`.
2. `backend/collectors/playwright/browser.py`:
   - `BrowserManager` controls Chromium lifecycle, browser context creation, and clean shutdown.
3. `backend/app/db/database.py` & `backend/app/db/models/`:
   - SQLAlchemy 2.x engine, session factory (`SessionLocal`), and relational schemas (`CollectionRun`, `FareObservation`, `RouteWeight`, etc.).
4. Existing tests:
   - 23 existing unit and integration tests passing in `backend/collectors/tests/`.

---

## B. Scheduler Architecture

The scheduler is implemented as a clean, decoupled layer that drives `CollectionOrchestrator` without duplicating any scraping or persistence logic:

```text
                    ┌───────────────────────────────┐
                    │           SCHEDULER           │
                    │       (APScheduler daemon)    │
                    │       Daily at 02:00 IST      │
                    └───────────────┬───────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │         SchedulerLock         │
                    │   (pg_try_advisory_lock)      │
                    │   Auto-releases on crash      │
                    └───────────────┬───────────────┘
                                    │ (Lock acquired)
                                    ▼
                    ┌───────────────────────────────┐
                    │        CollectionRunner       │
                    │   - Observation date (IST)    │
                    │   - Task matrix: 25 × 5 = 125 │
                    │   - Watchdog: 180 min max     │
                    └───────────────┬───────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │     CollectionOrchestrator    │
                    └───────────────┬───────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
       Yatra OTA              EaseMyTrip OTA         SpiceJet Direct
            │                       │                       │
            └───────────────────────┼───────────────────────┘
                                    │
                                    ▼
                               FlightQuote
                                    ▼
                             FareObservation
                                    ▼
                               PostgreSQL
```

---

## C. Files Created / Modified

| File | Type | Description |
| :--- | :--- | :--- |
| `backend/scheduler/config.py` | Created | `SchedulerConfig` dataclass, env var parser, Cleartrip disabled-by-default policy |
| `backend/scheduler/lock.py` | Created | `SchedulerLock` implementing PostgreSQL session-level advisory locking (`pg_try_advisory_lock`) |
| `backend/scheduler/runner.py` | Created | `CollectionRunner` coordinating observation date derivation, task matrix, execution loop, runtime watchdog, and resource cleanup |
| `backend/scheduler/scheduler.py` | Created | CLI entrypoint and APScheduler `BlockingScheduler` daemon supporting `--run-now`, `--dry-run`, and filters |
| `backend/scheduler/__init__.py` | Created | Public package exports and lazy `create_scheduler` factory |
| `backend/scheduler/tests/__init__.py` | Created | Test package marker |
| `backend/scheduler/tests/test_scheduler_lock.py` | Created | 7 unit and live PostgreSQL integration tests for `SchedulerLock` |
| `backend/scheduler/tests/test_scheduler.py` | Created | 14 unit tests covering config, timezone, task counts, dry-run, run-now, exception recovery, and CLI |
| `docs/scheduler.md` | Created | Comprehensive scheduler architectural and operational documentation |
| `README.md` | Modified | Added Automated Scraping Scheduler feature summary and usage commands |
| `scheduler_implementation_report.md` | Created | Final technical verification and audit report |

---

## D. Configuration

The scheduler supports environment variable configuration with resilient fallback defaults:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `PROJECT_TIMEZONE` | `Asia/Kolkata` | Timezone for scheduling and date arithmetic |
| `SCRAPE_SCHEDULE_HOUR` | `2` | Scheduled hour (0–23) |
| `SCRAPE_SCHEDULE_MINUTE` | `0` | Scheduled minute (0–59) |
| `SCRAPE_SOURCES` | `yatra,easemytrip,spicejet,air_india_express` | Active data sources |
| `SCRAPE_MAX_RUNTIME_MINUTES` | `180` | Maximum runtime watchdog (minutes) |
| `SCRAPE_LOCK_ID` | `847291` | 64-bit integer identifier for PostgreSQL advisory lock |
| `SCRAPE_TASK_DELAY_SECONDS` | `2.0` | Delay between consecutive tasks |
| `SCRAPE_HEADLESS` | `true` | Browser headless mode |

### Collector Policy:
- **Default Active**: `YATRA`, `EASEMYTRIP`, `SPICEJET`, `AIR_INDIA_EXPRESS`.
- **Default Inactive**: `CLEARTRIP` is **disabled by default** due to upstream Akamai bot detection (returns 403 on flight/search/v2) and will only execute if explicitly added to `SCRAPE_SOURCES`.

---

## E. Lock Mechanism

- **Implementation**: PostgreSQL Session-Level Advisory Lock (`SELECT pg_try_advisory_lock(847291)`).
- **Mutual Exclusion**:
  - If Lock is free: Process acquires lock, retains database connection for cycle duration, and proceeds.
  - If Lock is held: Attempt returns `False` immediately without blocking. Incoming process logs:
    ```text
    [WARNING] backend.scheduler.lock: Collection cycle skipped because another cycle is already running.
    ```
    and exits cleanly.
- **Crash Safety**: Session-level advisory locks are bound to the database TCP session. If a scheduler process crashes, terminates, or loses power, PostgreSQL automatically terminates the backend session and releases the advisory lock.
- **Explicit Cleanup**: Wrapped in `try ... finally` blocks to ensure release (`SELECT pg_advisory_unlock(847291)`) upon completion or failure.

---

## F. Test Results

### 1. Scheduler Unit & Integration Tests (`pytest backend/scheduler/tests/ -v`)
```text
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
collected 21 items

backend/scheduler/tests/test_scheduler.py::TestSchedulerConfig::test_default_configuration PASSED [  4%]
backend/scheduler/tests/test_scheduler.py::TestSchedulerConfig::test_timezone_handling PASSED [  9%]
backend/scheduler/tests/test_scheduler.py::TestSchedulerConfig::test_env_variable_overrides PASSED [ 14%]
backend/scheduler/tests/test_scheduler.py::TestSchedulerConfig::test_source_parsing_and_cleartrip_policy PASSED [ 19%]
backend/scheduler/tests/test_scheduler.py::TestTaskCalculation::test_task_matrix_counts PASSED [ 23%]
backend/scheduler/tests/test_scheduler.py::TestTaskCalculation::test_observation_date_derivation PASSED [ 28%]
backend/scheduler/tests/test_scheduler.py::TestDryRunBehavior::test_dry_run_no_browser_no_db_persistence PASSED [ 33%]
backend/scheduler/tests/test_scheduler.py::TestRunNowBehavior::test_run_now_successful_execution PASSED [ 38%]
backend/scheduler/tests/test_scheduler.py::TestRunNowBehavior::test_run_now_skipped_when_locked PASSED [ 42%]
backend/scheduler/tests/test_scheduler.py::TestRunNowBehavior::test_unexpected_exception_releases_lock PASSED [ 47%]
backend/scheduler/tests/test_scheduler.py::TestSchedulerStartupAndCLI::test_create_scheduler_job_registration PASSED [ 52%]
backend/scheduler/tests/test_scheduler.py::TestSchedulerStartupAndCLI::test_cli_parsing PASSED [ 57%]
backend/scheduler/tests/test_scheduler.py::TestSchedulerStartupAndCLI::test_main_dry_run_exit_code PASSED [ 61%]
backend/scheduler/tests/test_scheduler.py::TestSchedulerStartupAndCLI::test_main_run_now_exit_codes PASSED [ 66%]
backend/scheduler/tests/test_scheduler_lock.py::TestSchedulerLockUnit::test_lock_acquire_success PASSED [ 71%]
backend/scheduler/tests/test_scheduler_lock.py::TestSchedulerLockUnit::test_lock_acquire_already_held_by_another_process PASSED [ 76%]
backend/scheduler/tests/test_scheduler_lock.py::TestSchedulerLockUnit::test_lock_release PASSED [ 80%]
backend/scheduler/tests/test_scheduler_lock.py::TestSchedulerLockUnit::test_lock_context_manager PASSED [ 85%]
backend/scheduler/tests/test_scheduler_lock.py::TestSchedulerLockUnit::test_lock_release_on_exception_in_context_manager PASSED [ 90%]
backend/scheduler/tests/test_scheduler_lock.py::TestSchedulerLockUnit::test_lock_acquire_exception_handled PASSED [ 95%]
backend/scheduler/tests/test_scheduler_lock.py::TestSchedulerLockIntegration::test_live_postgres_mutual_exclusion PASSED [100%]

============================= 21 passed in 2.41s ==============================
```

### 2. Existing Collector Test Suite Regression Verification (`pytest backend/collectors/tests/ -v`)
```text
============================= 23 passed in 10.93s =============================
```
Zero regressions across existing audit and orchestrator components.

---

## G. Dry-Run Output

Command:
```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler --dry-run
```

Output:
```text
2026-09-17 18:51:27 [INFO] backend.collectors.orchestrator: Loaded 25 active basket routes for base_period_code='2026-07'
2026-09-17 18:51:27 [INFO] backend.collectors.orchestrator: Loaded 5 active booking windows from database
2026-09-17 18:51:27 [INFO] backend.collectors.orchestrator: Generated 125 collection tasks (25 routes × 5 windows) for observation_date=2026-09-17 (tz=Asia/Kolkata)
2026-09-17 18:51:27 [INFO] scheduler.runner: Dry-run plan generated successfully (500 tasks total across 4 sources).

==================================================
          COLLECTION DRY-RUN PLAN
==================================================
Timezone: Asia/Kolkata
Schedule: 02:00 daily
Observation Date: 2026-09-17
Routes: 25
Booking windows: 5
Tasks per source: 125
Configured sources:
  Yatra
  EaseMyTrip
  SpiceJet
  Air India Express

Total source tasks: 500

No scraping performed because --dry-run was specified.
==================================================
```

---

## H. Manual One-Shot Result

Executed a live smoke test invocation via the scheduler CLI:
```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler --run-now --route-limit 1 --window-codes T+7 --sources spicejet
```

Log Trace Highlights:
```text
2026-09-17 18:52:03 [INFO] scheduler: [SCHEDULER] Manual one-shot trigger initiated (--run-now).
2026-09-17 18:52:04 [INFO] backend.scheduler.lock: [LOCK] Successfully acquired scheduler advisory lock (847291).
2026-09-17 18:52:04 [INFO] scheduler.runner: [SCHEDULER] Collection cycle started
2026-09-17 18:52:04 [INFO] scheduler.runner: >>> [Source: SPICEJET | Task 1/1] Route: DEL-BOM | Window: T+7 | Date: 2026-09-24 <<<
2026-09-17 18:52:04 [INFO] backend.processing.ingestion.repository: Started CollectionRun: run_id=1202, source_id=24
2026-09-17 18:52:07 [INFO] backend.collectors.playwright.browser: Browser launched successfully.
2026-09-17 18:52:33 [INFO] spicejet_collector: Extracted flight: SG 162 | DEL -> BOM | Departure: 19:55:00 | Arrival: 22:40:00 | Fare: ₹6738
2026-09-17 18:52:33 [INFO] spicejet_collector: Extracted flight: SG 2802 | DEL -> BOM | Departure: 22:45:00 | Arrival: 01:25:00 | Fare: ₹8349
2026-09-17 18:52:33 [INFO] spicejet_collector: Successfully parsed 2 FlightQuote objects.
2026-09-17 18:52:34 [INFO] backend.processing.ingestion.repository: Completed CollectionRun: run_id=1202, status=COMPLETED, records=2
2026-09-17 18:52:34 [INFO] backend.processing.ingestion.repository: Ingestion completed for run 1202: 2 inserted, 0 skipped, 0 failed
2026-09-17 18:52:34 [INFO] scheduler.runner: <<< [SPICEJET #1] Status=COMPLETED, Quotes=2, Inserted=2, RunId=1202, Duration=30.11s >>>
2026-09-17 18:52:34 [INFO] backend.collectors.playwright.browser: Browser closed cleanly.
2026-09-17 18:52:34 [INFO] scheduler.runner: [SCHEDULER] Collector SPICEJET finished: 1 completed, 0 failed, 0 zero-inv, 2 persisted
2026-09-17 18:52:34 [INFO] backend.scheduler.lock: [LOCK] Successfully released scheduler advisory lock (847291).
2026-09-17 18:52:34 [INFO] scheduler.runner: [SCHEDULER] Collection cycle COMPLETED
2026-09-17 18:52:34 [INFO] scheduler.runner: [SCHEDULER] Duration: 30.38s (0.51 min)
2026-09-17 18:52:34 [INFO] scheduler.runner: [SCHEDULER] Tasks completed: 1 / 1
2026-09-17 18:52:34 [INFO] scheduler.runner: [SCHEDULER] Quotes extracted: 2
2026-09-17 18:52:34 [INFO] scheduler.runner: [SCHEDULER] Observations persisted: 2
```

---

## I. PostgreSQL Validation

PostgreSQL audit of the run and database tables:
1. **Orphan `RUNNING` Runs**: `0` across the entire database.
2. **CollectionRun `1202`**:
   - Status: `COMPLETED`
   - `records_scraped`: `2`
   - `source_id`: `24` (SpiceJet Direct)
3. **Persisted Observations (`fare_observations`)**:
   - Count: `2`
   - Persistence Parity: `2 == 2` (100% parity)
   - Observation 32729: Route `DEL-BOM` (ID 48), Window `T+7` (ID 2), Travel Date `2026-09-24`, Fare INR 6738.00
   - Observation 32730: Route `DEL-BOM` (ID 48), Window `T+7` (ID 2), Travel Date `2026-09-24`, Fare INR 8349.00
4. **Intra-run Duplicate Fingerprints**: `0` (verified unique per run).

---

## J. Known Limitations

1. **Cleartrip Inactivity**: Cleartrip is disabled by default because upstream Akamai bot detection returns 403 on `/flight/search/v2`. Can be re-enabled via `SCRAPE_SOURCES` once solved.
2. **Sequential Collector Execution**: Collectors run sequentially per source batch to prevent excessive local memory and browser process saturation.
3. **Windows PowerShell Execution**: On Windows, virtual environments use PowerShell execution policy requirements (prefix commands with `& backend\.venv\Scripts\python.exe`).

---

## K. Exact Commands to Operate the Scheduler

### Dry-Run:
```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler --dry-run
```

### Manual One-Shot (Full Cycle):
```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler --run-now
```

### Manual One-Shot (Fast Development Smoke Test):
```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler --run-now --route-limit 1 --window-codes T+7 --sources spicejet
```

### Recurring Scheduler Daemon:
```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler
```

### Stop Recurring Daemon:
Press `Ctrl+C` in the terminal to trigger graceful shutdown.
