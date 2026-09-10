# SIH Airfare Price Index

A real-time system for monitoring, indexing, and analyzing domestic airfare price trends across India.

## Tech Stack

- **Backend & Data Processing**:
  - Python 3.13+
  - FastAPI
  - SQLAlchemy 2.x & Alembic
  - PostgreSQL 16 (via Docker Compose)
  - psycopg 3
  - Pandas & NumPy
  - Playwright
- **Frontend**:
  - React & Vite
- **Infrastructure**:
  - Docker & Docker Compose

## Project Structure

```text
SIH-AIRFARE-PRICE-INDEX/
├── backend/
│   ├── alembic/              # Database migration scripts
│   ├── alembic.ini           # Alembic migration configuration
│   ├── app/
│   │   ├── db/
│   │   │   ├── base.py       # Declarative Base
│   │   │   ├── database.py   # Engine and session factory
│   │   │   ├── seed.py       # Seed data for booking windows
│   │   │   └── models/       # 13 SQLAlchemy 2.x models
│   ├── collectors/
│   │   └── playwright/       # Playwright browser automation foundation
│   ├── processing/
│   │   ├── cleaning/
│   │   └── index_engine/
│   ├── requirements.txt      # Pinned backend dependencies
│   └── tests/                # Test suite
├── frontend/
│   ├── public/
│   └── src/
├── data/
│   ├── raw/
│   └── processed/
├── docs/
├── docker-compose.yml        # PostgreSQL service definition
├── .env.example              # Environment variables template
├── .gitignore
├── pytest.ini
└── README.md
```

---

## Database Architecture

The database follows a **4-tier normalized relational architecture** implemented with SQLAlchemy 2.x and managed by Alembic:

### Tier 1: Master Reference Data
- **`cities`**: Core geographical nodes representing travel markets from DGCA statistics.
- **`airports`**: Optional supporting layer linking physical airport codes (e.g., DEL, BOM, GOI, GOX) to cities.
- **`routes`**: Directional city-pair markets (`origin_city_id` $\rightarrow$ `destination_city_id`).
- **`airlines`**: Domestic passenger carriers with extensible JSONB metadata.
- **`data_sources`**: Scrape endpoints (airline direct sites and OTAs).
- **`booking_windows`**: Configurable advance purchase windows (T+1, T+7, T+15, T+30, T+45).

### Tier 2: Pipeline & Audit
- **`collection_runs`**: Execution audit log tracking scrape jobs, status, and raw artifact storage paths.

### Tier 3: Observation & Pricing Layer
- **`fare_observations`**: Point-in-time flight price quotations. Scraper-tolerant with nullable fee breakdowns and times. Unique per collection run, source, and fare product fingerprint.

### Tier 4: Statistical & Index Layer
- **`dgca_traffic_data`**: Historical domestic passenger volume statistics from DGCA.
- **`route_weights`**: Approved, normalized route weights ($\sum w_i = 1.0$) for designated base periods.
- **`base_period_fares`**: Base-period benchmark fares ($P_{0, i, w}$).
- **`route_daily_summary`**: Aggregated representative daily route-window fares ($P_{t, i, w}$).
- **`index_daily`**: Published Airfare Price Index time series with expression-based coalesce uniqueness.

---

## Getting Started: Database Layer

### 1. Start PostgreSQL via Docker Compose

Ensure Docker Desktop is running, then start the PostgreSQL service:

```powershell
docker compose up -d postgres
```

Verify the container is healthy:

```powershell
docker compose ps
```

### 2. Activate Python Virtual Environment

From the project root:

```powershell
.\backend\.venv\Scripts\Activate.ps1
```

### 3. Configure Database URL

Copy `.env.example` to `.env` (already done for local development):

```env
POSTGRES_DB=airfare_index
POSTGRES_USER=airfare_user
POSTGRES_PASSWORD=airfare_password
POSTGRES_PORT=5432
DATABASE_URL=postgresql+psycopg://airfare_user:airfare_password@localhost:5432/airfare_index
```

### 4. Run Alembic Migrations

Apply the initial migration containing all 13 tables:

```powershell
& backend\.venv\Scripts\alembic.exe -c backend/alembic.ini upgrade head
```

### 5. Seed Static Booking Windows

Seed the 5 standard SIH booking windows (`T+1`, `T+7`, `T+15`, `T+30`, `T+45`):

```powershell
& backend\.venv\Scripts\python.exe -m backend.app.db.seed
```

*(Note: The seed script is completely idempotent and safe to re-run.)*

### 6. Run Database Tests & Verification

Verify the database connection, constraints, foreign keys, and nullability semantics:

```powershell
& backend\.venv\Scripts\pytest.exe -v backend/tests/test_database.py
```

---

## Data Collection Layer (Playwright POC)

### 1. Role of Playwright in the System

Playwright provides headless and headful browser automation to simulate user flight searches and collect real-time fare observations across domestic airlines (IndiGo, Air India, SpiceJet, etc.) and OTAs (MakeMyTrip, Yatra, EaseMyTrip, etc.).

Collected quotations are mapped directly to the `fare_observations` database table for downstream cleaning and price index calculation:

```text
Playwright Browser Automation
        ↓
Website-Specific Raw Extraction
        ↓
Normalized Observation Mapping
        ↓
fare_observations Database Table
        ↓
Statistical Cleaning & Filtering
        ↓
Representative Daily Fare
        ↓
Route & National Index Engine
```

> [!NOTE]
> This initial setup is a **browser-automation proof-of-concept**. Airline and OTA specific scraping modules will be added incrementally following the `BaseCollector` contract. The Playwright layer collects raw observations and does **not** perform index weighting, CPI calculation, or statistical cleaning.

### 2. Activate Python Virtual Environment

From the project root on Windows PowerShell:

```powershell
.\backend\.venv\Scripts\Activate.ps1
```

### 3. Run the Playwright Browser Test

The browser test verifies that Playwright can launch Chromium, open a target web page (`https://example.com`), extract metadata (page title, URL, and heading), and shut down cleanly.

**Direct CLI execution (opens visible Chromium browser window by default):**

```powershell
& backend\.venv\Scripts\python.exe -m backend.collectors.playwright.test_browser
```

*(To run headlessly from CLI, pass the `--headless` flag):*
```powershell
& backend\.venv\Scripts\python.exe -m backend.collectors.playwright.test_browser --headless
```

**Pytest execution:**

```powershell
& backend\.venv\Scripts\pytest.exe -v backend/collectors/playwright/test_browser.py
```

### 4. Expected Output

Running the test produces structured logging and prints metadata:

```text
2026-09-08 23:01:36,900 [INFO] test_browser: === Starting Playwright Browser Test ===
2026-09-08 23:01:36,901 [INFO] test_browser: Target URL: https://example.com | Headless: False
2026-09-08 23:01:36,901 [INFO] backend.collectors.playwright.browser: Browser starting: engine=chromium, headless=False, default_timeout=30000ms
2026-09-08 23:01:42,646 [INFO] backend.collectors.playwright.browser: Browser launched successfully.
2026-09-08 23:01:46,112 [INFO] backend.collectors.playwright.browser: Page opened.
2026-09-08 23:01:46,113 [INFO] test_browser: Navigating to https://example.com...

--- BROWSER TEST RESULTS ---
Page Title  : Example Domain
Current URL : https://example.com/
HTTP Status : 200
----------------------------

Heading Text: 'Example Domain'
2026-09-08 23:01:47,332 [INFO] test_browser: Browser control verified successfully: Title='Example Domain', URL='https://example.com/'
2026-09-08 23:01:47,333 [INFO] test_browser: Closing browser session cleanly...
2026-09-08 23:01:47,333 [INFO] backend.collectors.playwright.browser: Browser closing...
2026-09-08 23:01:50,514 [INFO] backend.collectors.playwright.browser: Browser closed cleanly.
2026-09-08 23:01:50,515 [INFO] test_browser: === Playwright Browser Test Completed ===
SUCCESS: Playwright browser test finished cleanly.
```

### 5. Configuration Settings

The browser layer reads the following optional environment variables (configured via `.env` or system environment):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PLAYWRIGHT_HEADLESS` | `false` | Set to `true` to run browsers without UI in production or background runs |
| `PLAYWRIGHT_TIMEOUT` | `30000` | Default navigation and selector timeout in milliseconds |
| `PLAYWRIGHT_BROWSER` | `chromium` | Browser engine (`chromium`, `firefox`, or `webkit`) |

