# Automated Scraping Scheduler

## 1. Overview & Purpose

The **Automated Scraping Scheduler** (`backend/scheduler`) automates daily domestic airfare data collection across India's domestic aviation network. It triggers the existing `CollectionOrchestrator` to gather airfare observations across the 25 DGCA basket routes and 5 booking windows (`T+1`, `T+7`, `T+15`, `T+30`, `T+45`) for all configured airline direct portals and Online Travel Agencies (OTAs).

The scheduler is decoupled from the scraping logic itself:
- **Scheduler's Responsibility**: Safely initiate exactly one collection cycle per day at the configured schedule, enforce process mutual exclusion to prevent overlapping runs, enforce a watchdog maximum runtime, and clean up all process resources.
- **Orchestrator's Responsibility**: Manage collector adapters, route/window matrix generation, browser execution, and database persistence.

```text
                    ┌───────────────────────────┐
                    │         SCHEDULER         │
                    │  (Daily 02:00 AM IST)     │
                    └─────────────┬─────────────┘
                                  │ (Acquires PG Advisory Lock)
                                  ▼
                    ┌───────────────────────────┐
                    │   CollectionOrchestrator  │
                    └─────────────┬─────────────┘
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

## 2. Schedule & Timezone

- **Default Schedule**: Daily at **02:00 AM IST** (`02:00:00+05:30`).
- **Default Timezone**: `Asia/Kolkata` (Indian Standard Time, UTC+05:30).
- **Observation Date**: Derived strictly from `datetime.now(ZoneInfo("Asia/Kolkata")).date()`. Naive machine local times are never used, guaranteeing that:
  $$\text{travel\_date} = \text{observation\_date} + \text{target\_advance\_days}$$

---

## 3. Environment Variables & Configuration

The scheduler reads settings from `.env` or system environment variables with resilient fallback defaults:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PROJECT_TIMEZONE` | `Asia/Kolkata` | Timezone for schedule triggers and observation dates |
| `SCRAPE_SCHEDULE_HOUR` | `2` | Hour of the day to trigger collection (0–23) |
| `SCRAPE_SCHEDULE_MINUTE` | `0` | Minute of the hour to trigger collection (0–59) |
| `SCRAPE_SOURCES` | `yatra,easemytrip,spicejet,air_india_express` | Comma-separated list of active collectors |
| `SCRAPE_MAX_RUNTIME_MINUTES` | `180` | Maximum runtime watchdog (minutes) before halting tasks |
| `SCRAPE_LOCK_ID` | `847291` | 64-bit integer identifier for PostgreSQL advisory lock |
| `SCRAPE_TASK_DELAY_SECONDS` | `2.0` | Polite inter-task delay between consecutive searches |
| `SCRAPE_HEADLESS` | `true` | Run browser in headless mode (`true` / `false`) |

### Collector Enable/Disable Policy
- **Active by default**:
  - `YATRA`: Yatra OTA
  - `EASEMYTRIP`: EaseMyTrip OTA
  - `SPICEJET`: SpiceJet Direct
  - `AIR_INDIA_EXPRESS`: Air India Express Direct
- **Disabled by default**:
  - `CLEARTRIP`: Cleartrip is **disabled by default** due to upstream Akamai bot detection. It will only execute if explicitly included in `SCRAPE_SOURCES` (e.g. `SCRAPE_SOURCES=yatra,easemytrip,spicejet,air_india_express,cleartrip`).

---

## 4. Mutual Exclusion & Process Locking

The scheduler implements strict process-level mutual exclusion using **PostgreSQL Session-Level Advisory Locks** (`pg_try_advisory_lock`):

1. **Non-Blocking Lock Acquisition**:
   Before initiating tasks, the runner attempts:
   ```sql
   SELECT pg_try_advisory_lock(847291);
   ```
2. **Duplicate Run Prevention**:
   If another collection cycle is active (or yesterday's cycle is still running), the query immediately returns `False`. The incoming process logs:
   ```text
   [WARNING] Collection cycle skipped because another cycle is already running.
   ```
   and exits cleanly without touching browsers or data.
3. **Automatic Crash Safety**:
   Unlike file-based locks or status-column flags that can become permanently orphaned if a process crashes, PostgreSQL session-level advisory locks are bound to the database TCP session. If the scheduler process is terminated, killed (`SIGKILL`), or loses power, PostgreSQL automatically drops the session and releases the advisory lock.
4. **Explicit Release**:
   In normal operation, the lock is released in a strict `try ... finally` block via `SELECT pg_advisory_unlock(847291);` when the cycle completes or fails.

---

## 5. CLI Usage (Windows PowerShell)

All commands are executed from the repository root: `C:\SIH-AIRFARE-PRICE-INDEX`.

### A. Dry-Run Mode (`--dry-run`)
Validates configuration, connects to the database, queries active basket routes and booking windows, builds the task matrix, and prints the collection plan without launching browsers or saving observations.

```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler --dry-run
```

**Sample Output**:
```text
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

### B. Manual One-Shot Mode (`--run-now`)
Executes exactly one collection cycle immediately and exits. Does not register a recurring schedule.

**Run full cycle across all configured sources:**
```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler --run-now
```

**Run development smoke test (filtered subset):**
```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler --run-now --route-limit 1 --window-codes T+7 --sources spicejet
```

Available CLI Filter Flags:
- `--route-limit N`: Limit to the first $N$ basket routes (e.g. `--route-limit 2`).
- `--window-codes W1,W2`: Filter specific booking windows (e.g. `--window-codes T+1,T+7`).
- `--sources S1,S2`: Override configured sources (e.g. `--sources spicejet,easemytrip`).
- `--date YYYY-MM-DD`: Manually specify observation date.

---

### C. Recurring Daemon Mode
Starts the long-running APScheduler process that triggers every night at 02:00 AM IST:

```powershell
& backend\.venv\Scripts\python.exe -m backend.scheduler.scheduler
```

**Startup Log**:
```text
============================================================
[SCHEDULER] SCHEDULER STARTED
[SCHEDULER] Timezone: Asia/Kolkata
[SCHEDULER] Schedule: 02:00 daily
[SCHEDULER] Configured sources: YATRA, EASEMYTRIP, SPICEJET, AIR_INDIA_EXPRESS
[SCHEDULER] Next scheduled run: 2026-09-18 02:00:00+05:30
[SCHEDULER] Press Ctrl+C to stop.
============================================================
```

To stop the scheduler, press `Ctrl+C`. The process catches `KeyboardInterrupt`, shuts down APScheduler, releases any locks held, and terminates cleanly.

---

## 6. Failure Recovery & Error Handling

1. **Task-Level Isolation**:
   If an individual scraping task fails (e.g. portal selector change, timeout), `CollectionOrchestrator` records that single task as `FAILED` in `collection_runs`, but continues executing remaining tasks in the batch.
2. **Watchdog Runtime Limit (`SCRAPE_MAX_RUNTIME_MINUTES`)**:
   If the cycle reaches 180 minutes, the watchdog halts further task dispatch, allows any in-flight browser task to finish gracefully, marks the cycle `TIMED_OUT`, and releases the lock.
3. **Unexpected Cycle Exceptions**:
   Any unhandled exception is caught, logged in detail, the lock is released in `finally`, and the scheduler process remains alive to execute the next day's cycle.
4. **No Orphan `RUNNING` Runs**:
   All database runs are managed via `IngestionRepository` with explicit transitions (`RUNNING` $\rightarrow$ `COMPLETED` or `FAILED`).

---

## 7. Production Deployment Recommendations

While development is hosted on Windows, in production the scheduler can run using one of the following standard patterns:

### Option 1: Linux systemd Service (Recommended for dedicated VM / VPS)
Create `/etc/systemd/system/airfare-scheduler.service`:
```ini
[Unit]
Description=SIH Airfare Price Index Automated Scraping Scheduler
After=network.target postgresql.service

[Service]
Type=simple
User=airfare
WorkingDirectory=/opt/SIH-AIRFARE-PRICE-INDEX
EnvironmentFile=/opt/SIH-AIRFARE-PRICE-INDEX/.env
ExecStart=/opt/SIH-AIRFARE-PRICE-INDEX/backend/.venv/bin/python -m backend.scheduler.scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable airfare-scheduler
sudo systemctl start airfare-scheduler
sudo systemctl status airfare-scheduler
```

### Option 2: Docker Container Supervision
When running containerized, run the scheduler as a dedicated service in `docker-compose.yml`:
```yaml
  scheduler:
    build:
      context: .
      dockerfile: backend/Dockerfile
    command: python -m backend.scheduler.scheduler
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
```

### Option 3: Windows Task Scheduler (Alternative on Windows Server)
Configure a task triggered at system startup or daily:
- Action: Start a program
- Program: `C:\SIH-AIRFARE-PRICE-INDEX\backend\.venv\Scripts\python.exe`
- Arguments: `-m backend.scheduler.scheduler`
- Start in: `C:\SIH-AIRFARE-PRICE-INDEX`
