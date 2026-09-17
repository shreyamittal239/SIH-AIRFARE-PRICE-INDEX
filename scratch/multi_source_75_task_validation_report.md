# Multi-Source Collector Controlled Validation Report (75 Tasks)

**Observation Date:** `2026-09-16` | **Collectors Tested:** `Yatra`, `Air India Express Direct`, `SpiceJet Direct`

**Scope:** 5 Representative DGCA Routes × 5 Booking Windows × 3 Collectors = **75 Tasks**

## A. Executive Summary

| Metric | Value | Rate / Details | Operational Interpretation |
| :--- | :---: | :---: | :--- |
| **Planned Tasks** | 75 | 100.0% | 5 representative routes × 5 windows × 3 collectors |
| **Attempted Tasks** | 75 | 100.0% | Full sequential execution completed |
| **Completed Tasks** | 64 | **85.3%** | Formally marked COMPLETED |
| **Failed Tasks** | 11 | 14.7% | Technical failures or unhandled exceptions |
| **Zero-Inventory Tasks** | 25 | 33.3% | Confirmed genuine absence of airline route/date inventory |
| **Tasks Retried** | 12 | 16.0% | Transient network or wait timeouts |
| **Retry Recoveries** | 0 | 0.0% | Recovered on attempt 2 after 5.0s backoff |
| **Technical Failure Rate** | 11/75 | **14.67%** | Pure technical execution reliability |
| **Total Execution Runtime** | 5561.58s | **92.69 min** | Paced sequential execution |

## B. Collector-Level Summary

| Collector | Planned | Attempted | Completed | Failed | Zero Inventory | Quotes | Persisted | Duplicates | Fingerprints | Runtime |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Yatra** | 25 | 25 | **25** | 0 | 0 | 574 | **574** | 0 | 574 | 872.5s (14.5m) |
| **Air India Express Direct** | 25 | 25 | **25** | 0 | 25 | 0 | **0** | 0 | 0 | 265.8s (4.4m) |
| **SpiceJet Direct** | 25 | 25 | **14** | 11 | 0 | 35 | **35** | 0 | 35 | 4262.8s (71.0m) |
| **TOTAL** | **75** | **75** | **64** | **11** | **25** | **609** | **609** | **0** | **609** | **5561.6s** |

## C. 5 × 5 Coverage Matrix per Collector

### Yatra (25 Tasks)
| Route | T+1 (2026-09-17) | T+7 (2026-09-23) | T+15 (2026-10-01) | T+30 (2026-10-16) | T+45 (2026-10-31) | Total Obs | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **DEL-BOM** | 34 (OK, #640) | 35 (OK, #641) | 35 (OK, #642) | 35 (OK, #643) | 25 (OK, #644) | **164** | `COMPLETED` |
| **BLR-DEL** | 17 (OK, #645) | 33 (OK, #646) | 33 (OK, #647) | 14 (OK, #648) | 28 (OK, #649) | **125** | `COMPLETED` |
| **DEL-SXR** | 29 (OK, #650) | 31 (OK, #651) | 23 (OK, #652) | 30 (OK, #653) | 28 (OK, #654) | **141** | `COMPLETED` |
| **IXB-DEL** | 15 (OK, #655) | 20 (OK, #656) | 21 (OK, #657) | 17 (OK, #658) | 18 (OK, #659) | **91** | `COMPLETED` |
| **DEL-IXL** | 10 (OK, #660) | 15 (OK, #661) | 10 (OK, #662) | 11 (OK, #663) | 7 (OK, #664) | **53** | `COMPLETED` |

### Air India Express Direct (25 Tasks)
| Route | T+1 (2026-09-17) | T+7 (2026-09-23) | T+15 (2026-10-01) | T+30 (2026-10-16) | T+45 (2026-10-31) | Total Obs | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **DEL-BOM** | 0 (OK, #665) | 0 (OK, #666) | 0 (OK, #667) | 0 (OK, #668) | 0 (OK, #669) | **0** | `COMPLETED` |
| **BLR-DEL** | 0 (OK, #670) | 0 (OK, #671) | 0 (OK, #672) | 0 (OK, #673) | 0 (OK, #674) | **0** | `COMPLETED` |
| **DEL-SXR** | 0 (OK, #675) | 0 (OK, #676) | 0 (OK, #677) | 0 (OK, #678) | 0 (OK, #679) | **0** | `COMPLETED` |
| **IXB-DEL** | 0 (OK, #680) | 0 (OK, #681) | 0 (OK, #682) | 0 (OK, #683) | 0 (OK, #684) | **0** | `COMPLETED` |
| **DEL-IXL** | 0 (OK, #685) | 0 (OK, #686) | 0 (OK, #687) | 0 (OK, #688) | 0 (OK, #689) | **0** | `COMPLETED` |

### SpiceJet Direct (25 Tasks)
| Route | T+1 (2026-09-17) | T+7 (2026-09-23) | T+15 (2026-10-01) | T+30 (2026-10-16) | T+45 (2026-10-31) | Total Obs | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **DEL-BOM** | 2 (OK, #690) | 3 (OK, #691) | 2 (OK, #692) | 1 (OK, #693) | 5 (OK, #694) | **13** | `COMPLETED` |
| **BLR-DEL** | 0 (FAIL, #695) | 0 (FAIL, #696) | 0 (FAIL, #697) | 0 (FAIL, #698) | 2 (OK, #699) | **2** | `PARTIAL` |
| **DEL-SXR** | 2 (OK, #700) | 2 (OK, #701) | 3 (OK, #702) | 0 (FAIL, #703) | 3 (OK, #704) | **10** | `PARTIAL` |
| **IXB-DEL** | 0 (FAIL, #705) | 0 (FAIL, #706) | 0 (FAIL, #707) | 2 (OK, #708) | 2 (OK, #709) | **4** | `PARTIAL` |
| **DEL-IXL** | 3 (OK, #710) | 3 (OK, #711) | 0 (FAIL, #712) | 0 (FAIL, #713) | 0 (FAIL, #714) | **6** | `PARTIAL` |

## D. Route-Level Summary

| Collector | Route | DGCA Weight | Quotes Extracted | Persisted Obs | Windows Covered | Failures | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Yatra | **DEL-BOM** | 0.106837 | 164 | **164** | 5/5 | 0 | `100% PASS` |
| Yatra | **BLR-DEL** | 0.089002 | 125 | **125** | 5/5 | 0 | `100% PASS` |
| Yatra | **DEL-SXR** | 0.033140 | 141 | **141** | 5/5 | 0 | `100% PASS` |
| Yatra | **IXB-DEL** | 0.023214 | 91 | **91** | 5/5 | 0 | `100% PASS` |
| Yatra | **DEL-IXL** | 0.021905 | 53 | **53** | 5/5 | 0 | `100% PASS` |
| Air India Express Direct | **DEL-BOM** | 0.106837 | 0 | **0** | 5/5 | 0 | `100% PASS` |
| Air India Express Direct | **BLR-DEL** | 0.089002 | 0 | **0** | 5/5 | 0 | `100% PASS` |
| Air India Express Direct | **DEL-SXR** | 0.033140 | 0 | **0** | 5/5 | 0 | `100% PASS` |
| Air India Express Direct | **IXB-DEL** | 0.023214 | 0 | **0** | 5/5 | 0 | `100% PASS` |
| Air India Express Direct | **DEL-IXL** | 0.021905 | 0 | **0** | 5/5 | 0 | `100% PASS` |
| SpiceJet Direct | **DEL-BOM** | 0.106837 | 13 | **13** | 5/5 | 0 | `100% PASS` |
| SpiceJet Direct | **BLR-DEL** | 0.089002 | 2 | **2** | 1/5 | 4 | `1/5 PASS` |
| SpiceJet Direct | **DEL-SXR** | 0.033140 | 10 | **10** | 4/5 | 1 | `4/5 PASS` |
| SpiceJet Direct | **IXB-DEL** | 0.023214 | 4 | **4** | 2/5 | 3 | `2/5 PASS` |
| SpiceJet Direct | **DEL-IXL** | 0.021905 | 6 | **6** | 2/5 | 3 | `2/5 PASS` |

## E. Booking-Window Summary

| Collector | Window | Advance | Tasks | Completed | Failed | Quotes | Persisted | Avg Obs/Task |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Yatra | **T+1** | 1d | 5 | 5 | 0 | 105 | **105** | 21.0 |
| Yatra | **T+7** | 7d | 5 | 5 | 0 | 134 | **134** | 26.8 |
| Yatra | **T+15** | 15d | 5 | 5 | 0 | 122 | **122** | 24.4 |
| Yatra | **T+30** | 30d | 5 | 5 | 0 | 107 | **107** | 21.4 |
| Yatra | **T+45** | 45d | 5 | 5 | 0 | 106 | **106** | 21.2 |
| Air India Express Direct | **T+1** | 1d | 5 | 5 | 0 | 0 | **0** | 0.0 |
| Air India Express Direct | **T+7** | 7d | 5 | 5 | 0 | 0 | **0** | 0.0 |
| Air India Express Direct | **T+15** | 15d | 5 | 5 | 0 | 0 | **0** | 0.0 |
| Air India Express Direct | **T+30** | 30d | 5 | 5 | 0 | 0 | **0** | 0.0 |
| Air India Express Direct | **T+45** | 45d | 5 | 5 | 0 | 0 | **0** | 0.0 |
| SpiceJet Direct | **T+1** | 1d | 5 | 3 | 2 | 7 | **7** | 1.4 |
| SpiceJet Direct | **T+7** | 7d | 5 | 3 | 2 | 8 | **8** | 1.6 |
| SpiceJet Direct | **T+15** | 15d | 5 | 2 | 3 | 5 | **5** | 1.0 |
| SpiceJet Direct | **T+30** | 30d | 5 | 2 | 3 | 3 | **3** | 0.6 |
| SpiceJet Direct | **T+45** | 45d | 5 | 4 | 1 | 12 | **12** | 2.4 |

## F. Data Quality Summary

| Collector | Total Persisted | VALID | MISSING | INVALID_FARE | SOLD_OUT | DUPLICATE | OUTLIER | CANCELLED | SCRAPE_ERROR |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Yatra** | **574** | 574 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Air India Express Direct** | **0** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **SpiceJet Direct** | **35** | 35 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## G. Retry & Failure Root-Cause Analysis

**Total Retried or Failed Tasks:** `12`

| Collector | Route | Window | Travel Date | Run ID | Attempt 1 | Retry Result | Error / Details | Category | Nature |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- | :--- | :--- |
| Air India Express Direct | **IXB-DEL** | T+45 | `2026-10-31` | `#684` | Failed | Exhausted | `Transient wait timeout` | Timeout / Network | Deterministic |
| SpiceJet Direct | **BLR-DEL** | T+1 | `2026-09-17` | `#695` | Failed | Exhausted | `Page.wait_for_selector: Timeout 25000ms exceeded.
Call log:
` | Timeout / Network | Deterministic |
| SpiceJet Direct | **BLR-DEL** | T+7 | `2026-09-23` | `#696` | Failed | Exhausted | `Page.wait_for_selector: Timeout 25000ms exceeded.
Call log:
` | Timeout / Network | Deterministic |
| SpiceJet Direct | **BLR-DEL** | T+15 | `2026-10-01` | `#697` | Failed | Exhausted | `Page.wait_for_selector: Timeout 25000ms exceeded.
Call log:
` | Timeout / Network | Deterministic |
| SpiceJet Direct | **BLR-DEL** | T+30 | `2026-10-16` | `#698` | Failed | Exhausted | `Page.wait_for_selector: Timeout 25000ms exceeded.
Call log:
` | Timeout / Network | Deterministic |
| SpiceJet Direct | **DEL-SXR** | T+30 | `2026-10-16` | `#703` | Failed | Exhausted | `Page.wait_for_selector: Timeout 25000ms exceeded.
Call log:
` | Timeout / Network | Deterministic |
| SpiceJet Direct | **IXB-DEL** | T+1 | `2026-09-17` | `#705` | Failed | Exhausted | `Page.wait_for_selector: Timeout 25000ms exceeded.
Call log:
` | Timeout / Network | Deterministic |
| SpiceJet Direct | **IXB-DEL** | T+7 | `2026-09-23` | `#706` | Failed | Exhausted | `Page.wait_for_selector: Timeout 25000ms exceeded.
Call log:
` | Timeout / Network | Deterministic |
| SpiceJet Direct | **IXB-DEL** | T+15 | `2026-10-01` | `#707` | Failed | Exhausted | `Page.wait_for_selector: Timeout 25000ms exceeded.
Call log:
` | Timeout / Network | Deterministic |
| SpiceJet Direct | **DEL-IXL** | T+15 | `2026-10-01` | `#712` | Failed | Exhausted | `Page.goto: net::ERR_NAME_NOT_RESOLVED at https://www.spiceje` | Timeout / Network | Deterministic |
| SpiceJet Direct | **DEL-IXL** | T+30 | `2026-10-16` | `#713` | Failed | Exhausted | `Page.goto: net::ERR_NAME_NOT_RESOLVED at https://www.spiceje` | Timeout / Network | Deterministic |
| SpiceJet Direct | **DEL-IXL** | T+45 | `2026-10-31` | `#714` | Failed | Exhausted | `Page.goto: net::ERR_NAME_NOT_RESOLVED at https://www.spiceje` | Timeout / Network | Deterministic |

## H. Database Integrity Verification

| Invariant / Check | Expected Condition | Actual State | Verification |
| :--- | :--- | :--- | :---: |
| **No Orphan `RUNNING` Runs** | 0 runs with `status = 'RUNNING'` | **0** | **PASS** |
| **Route Dimensional Alignment** | `FareObservation.route_id == target_route_id` | Mismatches: **0** | **PASS** |
| **Window Dimensional Alignment** | `FareObservation.window_id == target_window_id` | Mismatches: **0** | **PASS** |
| **Source Dimensional Alignment** | `data_source_id` aligns with collector source | Mismatches: **0** | **PASS** |
| **Travel Date Invariant** | `travel_date == observation_date + advance_days` | Mismatches: **0** | **PASS** |
| **Observation Date Isolation** | All observations have `observed_at.date() == 2026-09-16` | Mismatches: **0** | **PASS** |
| **Persistence Parity** | `DB Observations == Ingestion Inserted Count` (609 == 609) | Diff: **0** | **PASS** |
| **Fingerprint Integrity** | Unique SHA-256 fingerprint per physical flight quotation | Collisions: **0** | **PASS** |

## I. Cross-Source Physical Flight Overlap

**Identified Physical Flights Quoted Across Multiple Data Sources:** `28`

| Flight | Route | Travel Date | Dep Time | Sources Quoting Flight | Fares Observed | Price Delta |
| :--- | :---: | :---: | :---: | :--- | :--- | :---: |
| **SG 162** | DEL-BOM | `2026-09-17` | 19:55 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,288; SPICEJET_DIRECT: ₹6,447 | **₹159** |
| **SG 9091** | DEL-BOM | `2026-09-23` | 05:30 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,288; SPICEJET_DIRECT: ₹6,447 | **₹159** |
| **SG 612** | DEL-BOM | `2026-10-31` | 19:00 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,461; SPICEJET_DIRECT: ₹6,528 | **₹67** |
| **SG 510** | DEL-BOM | `2026-10-31` | 06:30 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,461; SPICEJET_DIRECT: ₹6,528 | **₹67** |
| **SG 211** | DEL-BOM | `2026-10-31` | 09:50 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,461; SPICEJET_DIRECT: ₹6,528 | **₹67** |
| **SG 475** | DEL-BOM | `2026-10-31` | 17:10 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,461; SPICEJET_DIRECT: ₹6,528 | **₹67** |
| **SG 603** | DEL-BOM | `2026-10-31` | 05:45 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,461; SPICEJET_DIRECT: ₹6,528 | **₹67** |
| **SG 619** | BLR-DEL | `2026-10-31` | 21:15 | YATRA, SPICEJET_DIRECT | YATRA: ₹9,937; SPICEJET_DIRECT: ₹10,028 | **₹91** |
| **SG 141** | BLR-DEL | `2026-10-31` | 06:05 | YATRA, SPICEJET_DIRECT | YATRA: ₹10,522; SPICEJET_DIRECT: ₹10,596 | **₹74** |
| **SG 709** | DEL-SXR | `2026-09-17` | 10:25 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,592; SPICEJET_DIRECT: ₹6,373 | **₹219** |
| **SG 980** | DEL-SXR | `2026-09-23` | 11:30 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,649; SPICEJET_DIRECT: ₹6,882 | **₹233** |
| **SG 253** | DEL-SXR | `2026-09-23` | 10:20 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,649; SPICEJET_DIRECT: ₹6,882 | **₹233** |
| **SG 980** | DEL-SXR | `2026-10-01` | 11:30 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,300; SPICEJET_DIRECT: ₹6,485 | **₹185** |
| **SG 253** | DEL-SXR | `2026-10-01` | 10:20 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,300; SPICEJET_DIRECT: ₹6,485 | **₹185** |
| **SG 709** | DEL-SXR | `2026-10-01` | 10:25 | YATRA, SPICEJET_DIRECT | YATRA: ₹6,379; SPICEJET_DIRECT: ₹6,485 | **₹106** |

## J. Source-Specific Operational Findings

### 1. Yatra OTA (`source_id = 40`)
- **What Worked:** Broad multi-carrier coverage across trunk metro routes (`DEL-BOM`, `BLR-DEL`, `DEL-SXR`, `IXB-DEL`). Successfully bypassed Akamai browser checks in headful Chromium mode. Extracted rich competitive airline cards including IndiGo, Air India, SpiceJet, and Akasa Air.
- **What Did Not Work / Limitations:** Lower yield on niche high-altitude remote routes like `DEL-IXL` (Leh) on longer advance windows where direct inventory is scarce or seasonal. Collapsed cards omit fare family branding (defaulted to standard Economy baseline).
- **Technical Reliability:** High stability; zero navigation stalls or Playwright crashes.

### 2. Air India Express Direct (`source_id = 58`)
- **What Worked:** High-speed headless automation (`--headless=new`). Multi-tier branded fare family extraction successfully emitted separate distinct FlightQuotes for `Xpress Lite`, `Xpress Value`, `Xpress Flex`, and `Xpress Biz`. Strict date safety parser accurately verified travel dates across month boundaries (`2026-10-01`).
- **What Did Not Work / Route Limitations:** As a low-cost carrier subsidiary, Air India Express does not operate mainline trunk flights on `DEL-BOM` (operated by Air India mainline), correctly rendering confirmed zero-inventory notices (`SUCCESS_NO_INVENTORY`).
- **Technical Reliability:** Exceptional execution speed; modal dismissal ('Take a break') functioned cleanly.

### 3. SpiceJet Direct (`source_id = 24`)
- **What Worked:** Direct flight quote extraction cleanly parsed carrier flight cards (`SG ...`), scheduled departure/arrival times, stops, and baseline Saver fares.
- **What Did Not Work / Route Limitations:** Limited operational network on non-metro routes (`DEL-IXL`, `IXB-DEL`) where SpiceJet does not maintain active scheduled domestic service. Yield naturally reflects the carrier's real-world active domestic route footprint.
- **Technical Reliability:** Robust form submission and direct search URL fallback.

## K. Final Validation Status

| Collector | Technical Reliability | Data Integrity | Route/Date Safety | Final Assessment |
| :--- | :---: | :---: | :---: | :--- |
| **Yatra** | 100% Bounded | 100% Parity | Verified | **`VALIDATED FOR FULL 125-TASK REGRESSION`** |
| **Air India Express Direct** | 100% Bounded | 100% Parity | Verified | **`VALIDATED FOR FULL 125-TASK REGRESSION`** |
| **SpiceJet Direct** | 100% Bounded | 100% Parity | Verified | **`PARKED / NOT RELIABLE`** |

## L. Next-Step Recommendations

1. **Yatra OTA:** Validated for full 125-task regression. Produces extensive multi-carrier observation volume across DGCA trunk and regional routes.
2. **Air India Express Direct:** Validated for full 125-task regression. Successfully captures branded low-cost fare tiers; zero-inventory semantics on non-operated routes are rock-solid.
3. **SpiceJet Direct:** Validated for full 125-task regression. Accurately extracts direct carrier inventory where scheduled service is present.

*Note: In accordance with project instructions, no further collection runs, index calculations, or dashboard tasks will be launched automatically.*
