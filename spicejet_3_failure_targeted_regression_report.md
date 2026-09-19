# SpiceJet Targeted 3-Failure Investigation & Regression Report

**Date:** 2026-09-17  
**Observation Date:** `2026-09-16`  
**Target Collector:** `SpiceJet Direct` (`SPICEJET`)  
**Scope:** Investigation, root-cause diagnosis, surgical repair, and targeted re-execution of the 3 previously failed SpiceJet tasks from the 375-task multi-source regression.

---

## A. Original Failure Summary

During the 375-task multi-source live regression, SpiceJet Direct completed 122 of 125 tasks with 83 confirmed zero-inventory tasks and 71 quotes persisted. Exactly 3 tasks failed:

| Task # | Route | Booking Window | Advance Days | Travel Date | Original Run ID | Error Message |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **1** | **DEL-HYD** | `T+45` | 45d | `2026-10-31` | `#956` | `Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.` |
| **2** | **CCU-BOM** | `T+7` | 7d | `2026-09-23` | `#1008` | `Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.` |
| **3** | **CCU-BOM** | `T+15` | 15d | `2026-10-01` | `#1009` | `Unexpected empty result: 0 flight quotes extracted and no zero-inventory banner detected.` |

The collector correctly classified these as `FAILED` rather than silently misclassifying them as zero inventory because no confirmed zero-inventory banner was detected.

---

## B. Investigation Evidence for Each Task

To investigate the live DOM state without modifying code, a diagnostic browser session was executed against each exact canonical search URL.

### 1. DEL-HYD — T+45 (`2026-10-31`)
- **Canonical URL:** `https://www.spicejet.com/search?from=DEL&to=HYD&tripType=1&departure=2026-10-31&adult=1&child=0&srCitizen=0&infant=0&currency=INR&redirectTo=/`
- **Page Title:** `SpiceJet - Flight Booking for Domestic and International, Cheap Air Tickets`
- **Page Readiness:** Reached stable search results without error modals or zero-inventory notices.
- **Rendered Flight Content:** Exactly 1 live flight card was rendered in the DOM:
  ```
  15:50
  DEL
  Flight Details
  6h 30m
  22:20
  HYD
  SG 617, SG 688
  Connecting,with halt at VNS
  ₹ 12,243
  Earn 400 Points
  N/A
  Not Available
  ₹ 15,183
  Earn 512 Points
  ```
- **Evidence Artifact:** Screenshot saved to `scratch/spicejet_diagnostic_output/DEL-HYD_T+45.png`; innerText saved to `scratch/spicejet_diagnostic_output/DEL-HYD_T+45_text.txt`.

### 2. CCU-BOM — T+7 (`2026-09-23`)
- **Canonical URL:** `https://www.spicejet.com/search?from=CCU&to=BOM&tripType=1&departure=2026-09-23&adult=1&child=0&srCitizen=0&infant=0&currency=INR&redirectTo=/`
- **Page Title:** `SpiceJet - Flight Booking for Domestic and International, Cheap Air Tickets`
- **Page Readiness:** Reached stable search results with active date strip and fare card.
- **Rendered Flight Content:** Exactly 1 live flight card was rendered in the DOM:
  ```
  23:20
  CCU
  Flight Details
  23h 20m
  22:40+1
  BOM
  SG 906, SG 162
  Connecting,with halt at DEL
  ₹ 18,246
  Earn 600 Points
  N/A
  Not Available
  ₹ 20,872
  Earn 700 Points
  ```
- **Evidence Artifact:** Screenshot saved to `scratch/spicejet_diagnostic_output/CCU-BOM_T+7.png`; innerText saved to `scratch/spicejet_diagnostic_output/CCU-BOM_T+7_text.txt`.

### 3. CCU-BOM — T+15 (`2026-10-01`)
- **Canonical URL:** `https://www.spicejet.com/search?from=CCU&to=BOM&tripType=1&departure=2026-10-01&adult=1&child=0&srCitizen=0&infant=0&currency=INR&redirectTo=/`
- **Page Title:** `SpiceJet - Flight Booking for Domestic and International, Cheap Air Tickets`
- **Page Readiness:** Reached stable search results with active fare card.
- **Rendered Flight Content:** Exactly 1 live flight card was rendered in the DOM:
  ```
  23:20
  CCU
  Flight Details
  23h 20m
  22:40+1
  BOM
  SG 906, SG 162
  Connecting,with halt at DEL
  ₹ 13,510
  Earn 420 Points
  N/A
  Not Available
  ₹ 16,403
  Earn 532 Points
  ```
- **Evidence Artifact:** Screenshot saved to `scratch/spicejet_diagnostic_output/CCU-BOM_T+15.png`; innerText saved to `scratch/spicejet_diagnostic_output/CCU-BOM_T+15_text.txt`.

---

## C. Root Cause Classification

**Root Cause:** **Category B — Deterministic scraper/selector issue**

### Technical Explanation:
1. **Single-Flight Regex Constraint:**
   In `FirstAirlineCollector.extract_fares()`, the browser DOM evaluation filtered flight number containers using:
   ```javascript
   const flightNumDivs = allDivs.filter(d => /^SG\s*\d{3,4}$/.test(d.innerText ? d.innerText.trim() : ''));
   ```
   This regular expression strictly matched single flight numbers (e.g. `SG 8168`, `SG 123`).
2. **Connecting Flight Representation:**
   On multi-leg connecting routes (`DEL-HYD` via Varanasi `VNS`, and `CCU-BOM` via Delhi `DEL`), SpiceJet renders both operating flight numbers in the flight number element separated by a comma (e.g., `SG 617, SG 688` and `SG 906, SG 162`).
3. **Extraction Failure:**
   Because `"SG 617, SG 688"` and `"SG 906, SG 162"` do not match `/^SG\s*\d{3,4}$/`, `flightNumDivs` evaluated to `[]` (empty list). Consequently, `extract_fares()` returned zero cards despite real inventory being displayed.
4. **Adapter Rejection:**
   Because zero cards were extracted and no explicit zero-inventory banner was present, the adapter correctly refused to report zero inventory and marked the run `FAILED`.

---

## D. Code Changes

Surgical modifications were applied to [`backend/collectors/playwright/collectors/first_airline_collector.py`](file:///c:/SIH-AIRFARE-PRICE-INDEX/backend/collectors/playwright/collectors/first_airline_collector.py):

### 1. `extract_fares()`
Updated flight number element filtering to match both single flight numbers and comma-separated multi-leg connecting flight sequences:
```javascript
// Before:
const flightNumDivs = allDivs.filter(d => /^SG\s*\d{3,4}$/.test(d.innerText ? d.innerText.trim() : ''));

// After:
const flightNumDivs = allDivs.filter(d => {
    const t = d.innerText ? d.innerText.trim() : '';
    return /^SG[\s-]*\d{3,4}([\s,]+SG[\s-]*\d{3,4})*$/i.test(t);
});
```

### 2. `navigate_search()`
Updated the readiness condition so that connecting flight strings are immediately recognized without waiting for default timeouts:
```javascript
// Before:
return /^SG[\s-]*\d{3,4}$/i.test(t) || (t.startsWith('SG ') && t.length < 15);

// After:
return /^SG[\s-]*\d{3,4}([\s,]+SG[\s-]*\d{3,4})*$/i.test(t) || (t.startsWith('SG ') && t.length < 35);
```

### 3. `parse_flight_card_lines()`
Added support for SpiceJet connecting flight halt notation to correctly assign `stops = 1`:
```python
# Before:
elif "1 stop" in lower:
    stops = 1
    break
elif "2 stop" in lower:
    stops = 2
    break

# After:
elif "1 stop" in lower or "1-stop" in lower:
    stops = 1
    break
elif "2 stop" in lower or "2-stop" in lower:
    stops = 2
    break
elif "connecting" in lower or "halt at" in lower:
    stops = 1
    break
```

---

## E. Automated Test Results

A focused regression test `test_6_connecting_flights_multi_leg_parsing` was added to [`backend/collectors/playwright/tests/test_first_airline_collector.py`](file:///c:/SIH-AIRFARE-PRICE-INDEX/backend/collectors/playwright/tests/test_first_airline_collector.py) testing:
- DEL-HYD connecting flight parsing (`SG 617, SG 688`, halt at VNS, fare ₹12,243)
- CCU-BOM connecting flight parsing (`SG 906, SG 162`, halt at DEL, next-day arrival `22:40+1`, fare ₹18,246)

### Test Execution Summary:
1. **SpiceJet Collector Unit Tests:**
   ```
   pytest backend/collectors/playwright/tests/test_first_airline_collector.py
   ======================== 15 passed, 1 skipped in 1.27s ========================
   ```
2. **Orchestrator Tests:**
   ```
   pytest backend/collectors/tests/test_orchestrator.py
   ============================= 15 passed in 5.20s ==============================
   ```
3. **Collection Audit Tests:**
   ```
   pytest backend/collectors/tests/test_collection_audit.py
   ============================= 8 passed in 2.06s ===============================
   ```
**Total:** **38 passed, 1 skipped (0 failures)**.

---

## F. Rerun Results

The 3 previously failed tasks were re-executed through `CollectionOrchestrator` using observation date `2026-09-16` and the standard retry policy (max 1 retry, 5.0s backoff):

| # | Route | Window | Travel Date | Run ID | Attempts | Status | Collection Status | Quotes Extracted | Inserted | Duration |
| :-: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1** | **DEL-HYD** | `T+45` | `2026-10-31` | `#1199` | 1 | `COMPLETED` | `SUCCESS_WITH_QUOTES` | 1 | 1 | 22.97s |
| **2** | **CCU-BOM** | `T+7` | `2026-09-23` | `#1200` | 1 | `COMPLETED` | `SUCCESS_WITH_QUOTES` | 1 | 1 | 26.30s |
| **3** | **CCU-BOM** | `T+15` | `2026-10-01` | `#1201` | 1 | `COMPLETED` | `SUCCESS_WITH_QUOTES` | 1 | 1 | 22.54s |

**Outcome:** All 3 tasks completed successfully on **Attempt 1** (0 retries required).

---

## G. Database Persistence Results (Read-Only Audit)

A post-run audit was executed in PostgreSQL across `run_id in (1199, 1200, 1201)`:

| Audit Check | Target / Invariant | Observed Metric | Verdict |
| :--- | :--- | :---: | :---: |
| **Collection Runs Created** | Exactly 3 runs created | 3 | **PASS** |
| **Orphan RUNNING Runs** | Exactly 0 runs in RUNNING state | 0 | **PASS** |
| **Route ID Integrity** | 0 route_id mismatches | 0 | **PASS** |
| **Window ID Integrity** | 0 window_id mismatches | 0 | **PASS** |
| **Source ID Integrity** | `data_source_id == 24` (SpiceJet Direct) | 0 mismatches | **PASS** |
| **Travel Date Invariant** | `observed_at + advance == travel_date` | 0 mismatches | **PASS** |
| **Persistence Parity** | Ingested quotes == DB observations | 3 == 3 | **PASS** |
| **Fingerprint Uniqueness** | 0 duplicate SHA-256 hashes | 0 duplicates | **PASS** |
| **Fare Positive Invariant** | `total_fare > 0` & `quality_status='VALID'` | 3/3 valid | **PASS** |

### Persisted Observation Details:
1. **Run #1199 (DEL-HYD T+45):**
   - Flight: `SG 617` | Dep: `15:50:00` | Arr: `22:20:00` | Stops: `1` | Fare: `₹ 12,243.00` | Fingerprint: `84847c38a42222cb...`
2. **Run #1200 (CCU-BOM T+7):**
   - Flight: `SG 906` | Dep: `23:20:00` | Arr: `22:40:00` | Stops: `1` | Fare: `₹ 18,593.00` | Fingerprint: `b4b8d334fc03f942...`
3. **Run #1201 (CCU-BOM T+15):**
   - Flight: `SG 906` | Dep: `23:20:00` | Arr: `22:40:00` | Stops: `1` | Fare: `₹ 13,510.00` | Fingerprint: `44e85282ef92f029...`

---

## H. Updated SpiceJet 125-Task Status

With the resolution of these 3 tasks, the final status for SpiceJet Direct across the complete 25 routes × 5 booking windows matrix is:

| Metric | Pre-Fix Baseline | Rerun Delta | Final 125-Task Metric |
| :--- | :---: | :---: | :---: |
| **Attempted Tasks** | 125 | — | **125** (100.0%) |
| **Completed Tasks** | 122 | +3 | **125** (**100.0%**) |
| **Technical Failures** | 3 | -3 | **0** (**0.00%**) |
| **Confirmed Zero-Inventory Tasks** | 83 | 0 | **83** (66.4%) |
| **Tasks with Live Quotes** | 39 | +3 | **42** (33.6%) |
| **Total Quotes Extracted** | 71 | +3 | **74** |
| **Total Observations Persisted** | 71 | +3 | **74** |
| **Persistence Parity** | 100.0% | — | **100.0% (74 == 74)** |
| **Distinct SHA-256 Fingerprints** | 71 | +3 | **74 (0 collisions)** |

All 125 SpiceJet route-window tasks have now completed successfully without technical failures.

---

## I. Final Recommendation

**Collector Classification Status:**

# `READY FOR COLLECTION`

The SpiceJet collector has demonstrated complete operational stability across all 25 active DGCA routes and 5 standard booking windows:
1. Genuine zero-inventory states are reliably and rapidly confirmed via dual-condition detection.
2. Single flight numbers and multi-leg connecting flights with halts are accurately extracted and parsed into valid `FlightQuote` and `FareObservation` records.
3. Zero technical failures remain across the 125-task matrix.
