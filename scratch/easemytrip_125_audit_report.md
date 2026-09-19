# 125-Task EaseMyTrip Full Collection Cycle Final Audit Report

**Observation Date:** `2026-09-16` | **Source:** `EASEMYTRIP_OTA (easemytrip_ota, ID 54)` | **Total Tasks:** `125`

## 1. Task Metrics

| Metric | Count | Percentage | Operational Note |
| :--- | :---: | :---: | :--- |
| **Planned Tasks** | 125 | 100.0% | 25 active DGCA routes × 5 booking windows |
| **Attempted Tasks** | 125 | 100.0% | Full sequential execution |
| **Completed Tasks** | 125 | 100.0% | Successfully extracted and persisted |
| **Failed Tasks** | 0 | 0.0% | Isolated task-level errors |
| **Zero Inventory Tasks** | 0 | 0.0% | Valid empty flights result |
| **Tasks Retried** | 1 | 0.8% | Transient network/navigation retries |
| **Retry Successes** | 1 | 0.8% | Recovered on 2nd attempt after 5s backoff |
| **Technical Failure Rate** | 0/125 | **0.00%** | Non-retryable / exhausted failures |

## 2. Runtime Metrics

| Metric | Duration | Note |
| :--- | :---: | :--- |
| **Total Duration** | 1930.38s (32.17 min) | Full 125-task sequential loop with 2.0s pacing |
| **Average Task Duration** | 13.45s | Mean elapsed time per collection task |
| **Median Task Duration** | 12.76s | Typical task execution duration |
| **Min Task Duration** | 7.54s | Fastest route evaluation |
| **Max Task Duration** | 55.50s | Longest single route duration |

**Abnormally Long-Running Tasks (>120s):** None. All tasks completed within standard bounded timeouts.

## 3. Collection Volume

| Metric | Value | Verification Note |
| :--- | :---: | :--- |
| **Total FlightQuotes Extracted** | 8,400 | Raw validated quotes parsed from page DOM |
| **Total Persisted FareObservations** | 6,954 | Stored in PostgreSQL `fare_observations` table |
| **Intra-run Duplicates Filtered** | 1,446 | Filtered at IngestionRepository layer |
| **Distinct Product Fingerprints** | 6,954 | Unique SHA-256 flight quotation hashes |
| **Database Duplicate Count** | 0 | Duplicate rows in database (`uq_fare_obs_run_source_fingerprint`) |
| **Persistence Parity** | 100% PASS | DB observations (6954) == Ingestion inserted (6954) |

## 4. Data Quality Distribution

| Quality Status | Count | Percentage | Ingestion Classification |
| :--- | :---: | :---: | :--- |
| `VALID` | 6,954 | 100.0% | Conforming to domain & ingestion invariants |
| `MISSING` | 0 | 0.0% | Conforming to domain & ingestion invariants |
| `INVALID_FARE` | 0 | 0.0% | Conforming to domain & ingestion invariants |
| `SOLD_OUT` | 0 | 0.0% | Conforming to domain & ingestion invariants |
| `DUPLICATE` | 0 | 0.0% | Conforming to domain & ingestion invariants |
| `OUTLIER` | 0 | 0.0% | Conforming to domain & ingestion invariants |
| `CANCELLED` | 0 | 0.0% | Conforming to domain & ingestion invariants |
| `SCRAPE_ERROR` | 0 | 0.0% | Conforming to domain & ingestion invariants |

## 5. Full 25 × 5 Coverage Matrix

| Route | T+1 (2026-09-17) | T+7 (2026-09-23) | T+15 (2026-10-01) | T+30 (2026-10-16) | T+45 (2026-10-31) | Total Obs | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **DEL-BOM** | 81 (OK, #512) | 116 (OK, #513) | 81 (OK, #514) | 113 (OK, #515) | 90 (OK, #516) | **481** | `COMPLETED` |
| **BLR-DEL** | 91 (OK, #517) | 110 (OK, #518) | 126 (OK, #519) | 121 (OK, #520) | 121 (OK, #521) | **569** | `COMPLETED` |
| **BLR-BOM** | 86 (OK, #522) | 89 (OK, #523) | 93 (OK, #524) | 83 (OK, #525) | 95 (OK, #526) | **446** | `COMPLETED` |
| **DEL-HYD** | 74 (OK, #527) | 76 (OK, #528) | 81 (OK, #529) | 88 (OK, #530) | 99 (OK, #531) | **418** | `COMPLETED` |
| **DEL-CCU** | 70 (OK, #532) | 77 (OK, #533) | 82 (OK, #534) | 95 (OK, #535) | 82 (OK, #536) | **406** | `COMPLETED` |
| **DEL-PNQ** | 81 (OK, #537) | 80 (OK, #538) | 87 (OK, #539) | 81 (OK, #540) | 69 (OK, #541) | **398** | `COMPLETED` |
| **BLR-PNQ** | 38 (OK, #542) | 45 (OK, #543) | 43 (OK, #544) | 35 (OK, #545) | 36 (OK, #546) | **197** | `COMPLETED` |
| **AMD-DEL** | 26 (OK, #547) | 25 (OK, #548) | 29 (OK, #549) | 26 (OK, #550) | 27 (OK, #551) | **133** | `COMPLETED` |
| **BLR-HYD** | 56 (OK, #552) | 72 (OK, #553) | 70 (OK, #554) | 69 (OK, #555) | 66 (OK, #556) | **333** | `COMPLETED` |
| **MAA-DEL** | 61 (OK, #557) | 67 (OK, #558) | 72 (OK, #559) | 79 (OK, #560) | 73 (OK, #561) | **352** | `COMPLETED` |
| **MAA-BOM** | 51 (OK, #562) | 57 (OK, #563) | 55 (OK, #564) | 61 (OK, #565) | 52 (OK, #566) | **276** | `COMPLETED` |
| **HYD-BOM** | 54 (OK, #567) | 53 (OK, #568) | 58 (OK, #569) | 58 (OK, #570) | 58 (OK, #571) | **281** | `COMPLETED` |
| **DEL-SXR** | 41 (OK, #572) | 38 (OK, #573) | 63 (OK, #574) | 58 (OK, #575) | 22 (OK, #576) | **222** | `COMPLETED` |
| **BLR-CCU** | 66 (OK, #577) | 71 (OK, #578) | 84 (OK, #579) | 90 (OK, #580) | 76 (OK, #581) | **387** | `COMPLETED` |
| **CCU-BOM** | 55 (OK, #582) | 63 (OK, #583) | 64 (OK, #584) | 71 (OK, #585) | 63 (OK, #586) | **316** | `COMPLETED` |
| **AMD-BOM** | 25 (OK, #587) | 20 (OK, #588) | 28 (OK, #589) | 22 (OK, #590) | 23 (OK, #591) | **118** | `COMPLETED` |
| **BLR-MAA** | 42 (OK, #592) | 49 (OK, #593) | 53 (OK, #594) | 51 (OK, #595) | 55 (OK, #596) | **250** | `COMPLETED` |
| **DEL-GAU** | 49 (OK, #597) | 49 (OK, #598) | 59 (OK, #599) | 56 (OK, #600) | 53 (OK, #601) | **266** | `COMPLETED` |
| **DEL-PAT** | 24 (OK, #602) | 33 (OK, #603) | 26 (OK, #604) | 38 (OK, #605) | 32 (OK, #606) | **153** | `COMPLETED` |
| **BLR-COK** | 24 (OK, #607) | 27 (OK, #608) | 35 (OK, #609) | 25 (OK, #610) | 28 (OK, #611) | **139** | `COMPLETED` |
| **DEL-LKO** | 36 (OK, #612) | 45 (OK, #613) | 41 (OK, #614) | 58 (OK, #615) | 26 (OK, #616) | **206** | `COMPLETED` |
| **IXB-DEL** | 21 (OK, #617) | 21 (OK, #618) | 21 (OK, #619) | 27 (OK, #620) | 26 (OK, #621) | **116** | `COMPLETED` |
| **DEL-IXL** | 23 (OK, #622) | 21 (OK, #623) | 19 (OK, #624) | 13 (OK, #625) | 8 (OK, #626) | **84** | `COMPLETED` |
| **COK-BOM** | 28 (OK, #627) | 22 (OK, #628) | 26 (OK, #629) | 26 (OK, #630) | 30 (OK, #631) | **132** | `COMPLETED` |
| **MAA-HYD** | 54 (OK, #632) | 55 (OK, #633) | 55 (OK, #634) | 60 (OK, #635) | 51 (OK, #636) | **275** | `COMPLETED` |

## 6. Route-Level Summary

| Route Code | Origin | Destination | DGCA Weight | Quotes Extracted | Persisted Obs | Windows Covered | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **DEL-BOM** | DEL | BOM | 0.106837 | 497 | **481** | 5/5 | `100% COMPLETE` |
| **BLR-DEL** | BLR | DEL | 0.089002 | 627 | **569** | 5/5 | `100% COMPLETE` |
| **BLR-BOM** | BLR | BOM | 0.067874 | 486 | **446** | 5/5 | `100% COMPLETE` |
| **DEL-HYD** | DEL | HYD | 0.056646 | 431 | **418** | 5/5 | `100% COMPLETE` |
| **DEL-CCU** | DEL | CCU | 0.051468 | 433 | **406** | 5/5 | `100% COMPLETE` |
| **DEL-PNQ** | DEL | PNQ | 0.051079 | 426 | **398** | 5/5 | `100% COMPLETE` |
| **BLR-PNQ** | BLR | PNQ | 0.040652 | 248 | **197** | 5/5 | `100% COMPLETE` |
| **AMD-DEL** | AMD | DEL | 0.039594 | 181 | **133** | 5/5 | `100% COMPLETE` |
| **BLR-HYD** | BLR | HYD | 0.038530 | 431 | **333** | 5/5 | `100% COMPLETE` |
| **MAA-DEL** | MAA | DEL | 0.038223 | 447 | **352** | 5/5 | `100% COMPLETE` |
| **MAA-BOM** | MAA | BOM | 0.037806 | 392 | **276** | 5/5 | `100% COMPLETE` |
| **HYD-BOM** | HYD | BOM | 0.036388 | 371 | **281** | 5/5 | `100% COMPLETE` |
| **DEL-SXR** | DEL | SXR | 0.033140 | 228 | **222** | 5/5 | `100% COMPLETE` |
| **BLR-CCU** | BLR | CCU | 0.032280 | 422 | **387** | 5/5 | `100% COMPLETE` |
| **CCU-BOM** | CCU | BOM | 0.031393 | 356 | **316** | 5/5 | `100% COMPLETE` |
| **AMD-BOM** | AMD | BOM | 0.030637 | 162 | **118** | 5/5 | `100% COMPLETE` |
| **BLR-MAA** | BLR | MAA | 0.029495 | 358 | **250** | 5/5 | `100% COMPLETE` |
| **DEL-GAU** | DEL | GAU | 0.027248 | 324 | **266** | 5/5 | `100% COMPLETE` |
| **DEL-PAT** | DEL | PAT | 0.025344 | 153 | **153** | 5/5 | `100% COMPLETE` |
| **BLR-COK** | BLR | COK | 0.024692 | 235 | **139** | 5/5 | `100% COMPLETE` |
| **DEL-LKO** | DEL | LKO | 0.023867 | 216 | **206** | 5/5 | `100% COMPLETE` |
| **IXB-DEL** | IXB | DEL | 0.023214 | 207 | **116** | 5/5 | `100% COMPLETE` |
| **DEL-IXL** | DEL | IXL | 0.021905 | 88 | **84** | 5/5 | `100% COMPLETE` |
| **COK-BOM** | COK | BOM | 0.021386 | 281 | **132** | 5/5 | `100% COMPLETE` |
| **MAA-HYD** | MAA | HYD | 0.021300 | 400 | **275** | 5/5 | `100% COMPLETE` |

## 7. Failure Root-Cause Analysis

🎉 **Zero failed tasks! All 125 tasks completed successfully with 100% resilience.**

## 8. Database Integrity Verification

| Invariant / Check | Expected Condition | Actual State | Verification |
| :--- | :--- | :--- | :---: |
| **No Orphaned `RUNNING` Runs** | 0 runs with `status = 'RUNNING'` | **0** | **PASS** |
| **Route Dimensional Alignment** | `FareObservation.route_id == CollectionRun.target_route_id` | Mismatches: **0** | **PASS** |
| **Window Dimensional Alignment** | `FareObservation.window_id == CollectionRun.target_window_id` | Mismatches: **0** | **PASS** |
| **Data Source Foreign Key** | All rows reference `source_id = 54` (`EASEMYTRIP_OTA`) | Mismatches: **0** | **PASS** |
| **Travel Date Invariant** | `travel_date == observation_date + advance_days` | Mismatches: **0** | **PASS** |
| **Observation Date Isolation** | All rows have `observed_at.date() == 2026-09-16` | Mismatches: **0** | **PASS** |
| **Persistence Parity** | DB Observation Count == Ingestion Inserted Count | Diff: **0** | **PASS** |
| **Cross-Task Fingerprint Purity** | Zero cross-task collision or duplicate DB hashes | Duplicates: **0** | **PASS** |

## 9. Comparison Against Previous 125-Task Collection Run

| Benchmark Metric | Previous Run (Pre-Fix) | Current Run (Post-Fix) | Net Delta | Improvement Impact |
| :--- | :---: | :---: | :---: | :--- |
| **Attempted Tasks** | 125 | 125 | 0 | Same complete 25×5 scope |
| **Completed Tasks** | 51 (40.8%) | **125 (100.0%)** | **+74** | Substantial yield elevation |
| **Failed Tasks** | 74 (59.2%) | **0 (0.0%)** | **-74** | Resolution of validation defects |
| **FlightQuotes Extracted** | 3,262 | **8,400** | **+5,138** | Deep quote volume expansion |
| **Persisted Observations** | 2,754 | **6,954** | **+4,200** | Direct database population gain |
| **Intra-run Duplicates Filtered** | 508 | **1,446** | +938 | Deduplication parity maintained |

### Failure Category Delta Analysis:

1. **`T+15` October Date Regex Bug (`Sept?` assumption):**
   - *Previous:* 25 tasks failed across all routes when advancing 15 days to `2026-10-01` due to hardcoded September matching.
   - *Current:* **DISAPPEARED.** The generic multi-format date parser parsed and accepted all `2026-10-01` dates cleanly.
2. **City Name Aliases (`BLR`, `HYD`, `IXB`, `IXL`):**
   - *Previous:* 48 tasks failed because EaseMyTrip renders `Bengaluru` instead of `Bangalore`, `Hyderbad` instead of `Hyderabad`, and lacked explicit aliases for `Bagdogra` (`IXB`) and `Leh` (`IXL`).
   - *Current:* **DISAPPEARED.** All 4 city aliases are now recognized via word-boundary token matching.
3. **Network / Timeout Anomalies:**
   - *Previous:* Task #21 hit a DNS resolution failure (`net::ERR_NAME_NOT_RESOLVED`), retried, and was delayed by an external OS sleep.
   - *Current:* 0 technical failures observed. All network operations strictly bounded to <=35s timeouts.
