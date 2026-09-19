# DGCA Route Basket Data: Provenance & Scope

## 1. Input File
- **Path**: `data/dgca/dgca_july_2026_top25_route_basket.xlsx`
- **Content**: Pre-derived Top-25 domestic route basket for July 2026.

## 2. Source Provenance & Access Layer
- **Source Organization**: **Directorate General of Civil Aviation (DGCA)**, Government of India.
  - Official Monthly Aviation Statistics: [DGCA Statistics Portal](https://www.dgca.gov.in/digigov-portal/?page=jsp%2Fdgca%2FInventoryList%2FdataReports%2FaviationDataStatistics%2FairTransport%2Fdomestic%2Fmonthly%2Findex.html)
- **Machine-Readable Access / Cross-Check Layer**: **Dataful**
  - Dataset #23652: [Domestic Passenger Traffic for Scheduled Domestic Services](https://dataful.in/datasets/23652/)

## 3. Scope & Demonstration Boundaries
- **Dataset Scope**:
  The current spreadsheet contains the **already-derived Top-25 July 2026 routes**. It is **NOT** the complete national DGCA dataset (which contains 600+ city pairs).
- **What this implementation demonstrates**:
  ```
  Provided DGCA-sourced traffic data
      ↓
  City normalization
      ↓
  Undirected route resolution
      ↓
  DGCA traffic persistence (dgca_traffic_data)
      ↓
  Prototype route weights persistence (route_weights)
  ```
- **What this implementation does NOT yet demonstrate**:
  ```
  Complete national DGCA dataset (all 600+ city pairs)
      ↓
  Automatic ranking of all national routes
      ↓
  Automatic Top-25 selection
  ```

## 4. Weight Classification & Terminology
- The weights stored in `route_weights` are strictly:
  **"DGCA-traffic-derived prototype route weights"** or **"prototype route-basket weights derived from DGCA traffic"**.
- **DO NOT** refer to these as "official CPI weights".

## 5. Metric Definitions
- `share_of_total_traffic` = route traffic / total traffic across all available city pairs (~35.81% for Top-25).
- `basket_weight` = route traffic / total traffic within the selected Top-25 basket (100.00% for Top-25).
