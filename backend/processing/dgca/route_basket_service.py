"""DGCA-derived Top-25 route basket ingestion service.

DATA PROVENANCE & SCOPE NOTES:
1. Input File:
   data/dgca/dgca_july_2026_top25_route_basket.xlsx
   Contains the already-derived Top-25 July 2026 routes.

2. Complete vs Prototype Dataset Scope:
   This file is NOT the complete DGCA city-pair dataset (which covers 600+ city pairs).
   Therefore, the current implementation demonstrates:
       provided DGCA-sourced traffic data
       → route resolution
       → traffic storage
       → prototype route weights
   It does NOT yet demonstrate:
       complete national DGCA dataset
       → automatic ranking of all routes
       → automatic Top-25 selection.

3. Source Organization vs Access Layer:
   - Source Organization: Directorate General of Civil Aviation (DGCA)
     (Official monthly statistics portal: https://www.dgca.gov.in/)
   - Access Layer / Cross-Check: Dataful (Machine-readable dataset #23652: https://dataful.in/datasets/23652/)

4. Weight Classification & Terminology:
   The resulting weights are strictly:
   "DGCA-traffic-derived prototype route weights"
   (or "prototype route-basket weights derived from DGCA traffic").
   They are NOT "official CPI weights".

Handles:
- Loading and schema validation of the DGCA Top-25 Excel spreadsheet
- City normalization to master City records
- Undirected city-pair route resolution (preventing duplicate inverse routes)
- Idempotent persistence to dgca_traffic_data
- Idempotent persistence to route_weights
- Explicit metric distinction between share_of_total_traffic and basket_weight
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import select, or_, and_
from sqlalchemy.orm import Session

from backend.app.db.database import SessionLocal
from backend.app.db.models.city import City
from backend.app.db.models.route import Route
from backend.app.db.models.dgca_traffic_data import DGCATrafficData
from backend.app.db.models.route_weight import RouteWeight
from backend.processing.dgca.excel_reader import load_raw_route_basket

logger = logging.getLogger(__name__)

# Default source Excel location
DEFAULT_EXCEL_PATH = Path("data/dgca/dgca_july_2026_top25_route_basket.xlsx")

# Provenance and Period Constants
REFERENCE_YEAR = 2026
REFERENCE_MONTH = 7
BASE_PERIOD_CODE = "2026-07"
PERIOD_TYPE = "MONTHLY"
PERIOD_START = date(2026, 7, 1)
PERIOD_END = date(2026, 7, 31)
REPORT_SOURCE = "DGCA July 2026 (Dataful #23652 verified Top-25 prototype)"

# Known City Aliases & Metadata for Top-25 Basket
# Format: code: (city_code, city_name, state_name, is_metro)
CITY_MASTER_DATA = {
    "DEL": ("DEL", "Delhi", "Delhi", True),
    "BOM": ("BOM", "Mumbai", "Maharashtra", True),
    "BLR": ("BLR", "Bengaluru", "Karnataka", True),
    "MAA": ("MAA", "Chennai", "Tamil Nadu", True),
    "CCU": ("CCU", "Kolkata", "West Bengal", True),
    "HYD": ("HYD", "Hyderabad", "Telangana", True),
    "PNQ": ("PNQ", "Pune", "Maharashtra", False),
    "AMD": ("AMD", "Ahmedabad", "Gujarat", False),
    "SXR": ("SXR", "Srinagar", "Jammu and Kashmir", False),
    "GAU": ("GAU", "Guwahati", "Assam", False),
    "PAT": ("PAT", "Patna", "Bihar", False),
    "COK": ("COK", "Kochi", "Kerala", False),
    "LKO": ("LKO", "Lucknow", "Uttar Pradesh", False),
    "IXB": ("IXB", "Bagdogra", "West Bengal", False),
    "IXL": ("IXL", "Leh", "Ladakh", False),
    "GOI": ("GOI", "Goa", "Goa", False),
}

# Mapping of common city names and aliases to IATA city codes
CITY_NAME_TO_CODE = {
    "DELHI": "DEL",
    "NEW DELHI": "DEL",
    "MUMBAI": "BOM",
    "BOMBAY": "BOM",
    "BENGALURU": "BLR",
    "BANGALORE": "BLR",
    "CHENNAI": "MAA",
    "MADRAS": "MAA",
    "KOLKATA": "CCU",
    "CALCUTTA": "CCU",
    "HYDERABAD": "HYD",
    "PUNE": "PNQ",
    "AHMEDABAD": "AMD",
    "SRINAGAR": "SXR",
    "GUWAHATI": "GAU",
    "PATNA": "PAT",
    "KOCHI": "COK",
    "COCHIN": "COK",
    "LUCKNOW": "LKO",
    "BAGDOGRA": "IXB",
    "LEH": "IXL",
    "GOA": "GOI",
}


@dataclass
class RouteBasketItem:
    """Represents a single verified DGCA route basket row."""

    rank: int
    city_1: str
    city_2: str
    passengers_to_city_2: int
    passengers_from_city_2: int
    total_route_traffic: int
    share_of_total_traffic: Decimal
    basket_weight: Decimal
    reference_period: str
    source_organization: str
    access_layer: str
    route_id: Optional[int] = None
    route_code: Optional[str] = None
    origin_city_code: Optional[str] = None
    destination_city_code: Optional[str] = None


@dataclass
class IngestionSummary:
    """Summary metrics of the DGCA route basket ingestion."""

    total_basket_traffic: int
    total_basket_share: Decimal
    total_basket_weight_raw: Decimal
    total_basket_weight_rounded: Decimal
    route_count: int
    reference_year: int
    reference_month: int
    base_period_code: str
    routes_created: int
    routes_reused: int
    traffic_records_upserted: int
    weight_records_upserted: int
    items: List[RouteBasketItem] = field(default_factory=list)


class DGCARouteBasketService:
    """Service to load, resolve, and persist DGCA-derived route basket weights."""

    def __init__(self, db: Session) -> None:
        """Initialize the route basket service with an active DB session."""
        self.db = db

    def normalize_city(self, city_name: str) -> City:
        """Resolve or create a City record from a city name or alias.

        Args:
            city_name: Name of the city (e.g., 'Delhi', 'Bengaluru', 'Ahmedabad').

        Returns:
            Existing or newly created City record.
        """
        clean_name = city_name.strip()
        upper_name = clean_name.upper()

        # Determine target city code
        target_code = CITY_NAME_TO_CODE.get(upper_name, upper_name[:3])

        # Check if City already exists in DB by code or name
        stmt = select(City).where(
            or_(
                City.city_code == target_code,
                City.city_name.ilike(clean_name),
            )
        )
        city = self.db.scalars(stmt).first()

        if city is None:
            # Look up metadata or fallback
            code, off_name, state, is_metro = CITY_MASTER_DATA.get(
                target_code, (target_code, clean_name, "India", False)
            )
            logger.info("Creating City master record: code=%s, name=%s, state=%s", code, off_name, state)
            city = City(
                city_code=code,
                city_name=off_name,
                state_name=state,
                is_metro=is_metro,
                is_active=True,
            )
            self.db.add(city)
            self.db.flush()

        return city

    def resolve_or_create_route(self, city_1: City, city_2: City) -> Tuple[Route, bool]:
        """Find an existing undirected Route between two cities or create a new one.

        Routes for the basket are city-pair centric (e.g. DEL-BOM is the same undirected
        route as BOM-DEL). Reuses existing routes regardless of direction.

        Args:
            city_1: First city.
            city_2: Second city.

        Returns:
            Tuple of (Route record, was_created: bool).
        """
        if city_1.city_id == city_2.city_id:
            raise ValueError(f"Origin and destination cities cannot be identical: {city_1.city_code}")

        # Check for existing route in either direction
        stmt = select(Route).where(
            or_(
                and_(
                    Route.origin_city_id == city_1.city_id,
                    Route.destination_city_id == city_2.city_id,
                ),
                and_(
                    Route.origin_city_id == city_2.city_id,
                    Route.destination_city_id == city_1.city_id,
                ),
            )
        )
        existing_route = self.db.scalars(stmt).first()

        if existing_route is not None:
            logger.debug("Reusing existing route %s (ID %s) for city pair %s-%s",
                         existing_route.route_code, existing_route.route_id, city_1.city_code, city_2.city_code)
            return existing_route, False

        # Create new route following City 1 -> City 2 canonical orientation
        route_code = f"{city_1.city_code}-{city_2.city_code}"
        logger.info("Creating new Route entity: %s (origin=%s, dest=%s)", route_code, city_1.city_code, city_2.city_code)
        new_route = Route(
            origin_city_id=city_1.city_id,
            destination_city_id=city_2.city_id,
            route_code=route_code,
            is_active=True,
        )
        self.db.add(new_route)
        self.db.flush()

        return new_route, True

    def upsert_traffic_data(
        self,
        route_id: int,
        passengers_carried: int,
        report_source: str = REPORT_SOURCE,
    ) -> Tuple[DGCATrafficData, bool]:
        """Idempotently insert or update monthly DGCA traffic record for a route.

        Args:
            route_id: Database route ID.
            passengers_carried: Total passengers carried for the period.
            report_source: Provenance string.

        Returns:
            Tuple of (DGCATrafficData, was_created: bool).
        """
        stmt = select(DGCATrafficData).where(
            and_(
                DGCATrafficData.route_id == route_id,
                DGCATrafficData.period_type == PERIOD_TYPE,
                DGCATrafficData.period_start == PERIOD_START,
                DGCATrafficData.period_end == PERIOD_END,
            )
        )
        existing = self.db.scalars(stmt).first()

        if existing is not None:
            existing.passengers_carried = passengers_carried
            existing.report_source = report_source
            self.db.flush()
            return existing, False

        traffic = DGCATrafficData(
            route_id=route_id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            period_type=PERIOD_TYPE,
            passengers_carried=passengers_carried,
            flights_operated=None,
            report_source=report_source,
        )
        self.db.add(traffic)
        self.db.flush()
        return traffic, True

    def upsert_route_weight(
        self,
        route_id: int,
        passenger_volume: int,
        basket_weight: Decimal,
        base_period_code: str = BASE_PERIOD_CODE,
    ) -> Tuple[RouteWeight, bool]:
        """Idempotently insert or update route weight record.

        Args:
            route_id: Database route ID.
            passenger_volume: Route passenger traffic within base period.
            basket_weight: Derived basket weight (quantized to 8 decimals).
            base_period_code: Base period code (default: '2026-07').

        Returns:
            Tuple of (RouteWeight, was_created: bool).
        """
        # Quantize to 8 decimals matching Numeric(10, 8)
        quantized_weight = basket_weight.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

        stmt = select(RouteWeight).where(
            and_(
                RouteWeight.route_id == route_id,
                RouteWeight.base_period_code == base_period_code,
            )
        )
        existing = self.db.scalars(stmt).first()

        if existing is not None:
            existing.passenger_volume = passenger_volume
            existing.weight = quantized_weight
            existing.is_active = True
            self.db.flush()
            return existing, False

        weight_rec = RouteWeight(
            route_id=route_id,
            base_period_code=base_period_code,
            passenger_volume=passenger_volume,
            weight=quantized_weight,
            is_active=True,
        )
        self.db.add(weight_rec)
        self.db.flush()
        return weight_rec, True

    def ingest_route_basket(
        self,
        excel_path: Union[str, Path] = DEFAULT_EXCEL_PATH,
        commit: bool = True,
    ) -> IngestionSummary:
        """Load DGCA Top-25 Excel, resolve entities, and idempotently persist to DB.

        Args:
            excel_path: Path to the Top-25 Excel file.
            commit: Whether to commit transaction on completion.

        Returns:
            IngestionSummary containing metrics and processed items.
        """
        raw_rows = load_raw_route_basket(excel_path)
        if len(raw_rows) != 25:
            logger.warning("Expected 25 rows in Top-25 route basket, found %d", len(raw_rows))

        items: List[RouteBasketItem] = []
        routes_created = 0
        routes_reused = 0
        traffic_upserted = 0
        weight_upserted = 0

        for row in raw_rows:
            # 1. Resolve cities
            city_1 = self.normalize_city(row["city_1"])
            city_2 = self.normalize_city(row["city_2"])

            # 2. Resolve or create undirected route
            route, was_created = self.resolve_or_create_route(city_1, city_2)
            if was_created:
                routes_created += 1
            else:
                routes_reused += 1

            # 3. Upsert DGCA traffic record
            self.upsert_traffic_data(
                route_id=route.route_id,
                passengers_carried=row["total_route_traffic"],
                report_source=REPORT_SOURCE,
            )
            traffic_upserted += 1

            # 4. Upsert Route Weight record
            self.upsert_route_weight(
                route_id=route.route_id,
                passenger_volume=row["total_route_traffic"],
                basket_weight=row["basket_weight"],
                base_period_code=BASE_PERIOD_CODE,
            )
            weight_upserted += 1

            item = RouteBasketItem(
                rank=row["rank"],
                city_1=row["city_1"],
                city_2=row["city_2"],
                passengers_to_city_2=row["passengers_to_city_2"],
                passengers_from_city_2=row["passengers_from_city_2"],
                total_route_traffic=row["total_route_traffic"],
                share_of_total_traffic=row["share_of_total_traffic"],
                basket_weight=row["basket_weight"],
                reference_period=row["reference_period"],
                source_organization=row["source_organization"],
                access_layer=row["access_layer"],
                route_id=route.route_id,
                route_code=route.route_code,
                origin_city_code=city_1.city_code,
                destination_city_code=city_2.city_code,
            )
            items.append(item)

        if commit:
            self.db.commit()

        # Compute summary metrics
        total_traffic = sum(item.total_route_traffic for item in items)
        total_share = sum(item.share_of_total_traffic for item in items)
        total_weight_raw = sum(item.basket_weight for item in items)
        total_weight_rounded = sum(
            item.basket_weight.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
            for item in items
        )

        return IngestionSummary(
            total_basket_traffic=total_traffic,
            total_basket_share=total_share,
            total_basket_weight_raw=total_weight_raw,
            total_basket_weight_rounded=total_weight_rounded,
            route_count=len(items),
            reference_year=REFERENCE_YEAR,
            reference_month=REFERENCE_MONTH,
            base_period_code=BASE_PERIOD_CODE,
            routes_created=routes_created,
            routes_reused=routes_reused,
            traffic_records_upserted=traffic_upserted,
            weight_records_upserted=weight_upserted,
            items=items,
        )


def run_ingestion(excel_path: Union[str, Path] = DEFAULT_EXCEL_PATH) -> IngestionSummary:
    """Standalone runner function to execute DGCA route basket ingestion."""
    with SessionLocal() as session:
        service = DGCARouteBasketService(session)
        summary = service.ingest_route_basket(excel_path=excel_path, commit=True)
        return summary


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    excel_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXCEL_PATH
    print(f"=== Starting DGCA Route Basket Ingestion from {excel_file} ===")
    summary = run_ingestion(excel_file)
    print("=== Ingestion Completed Successfully ===")
    print(f"Routes processed: {summary.route_count}")
    print(f"Routes created: {summary.routes_created}, Routes reused: {summary.routes_reused}")
    print(f"DGCA Traffic records upserted: {summary.traffic_records_upserted}")
    print(f"Route Weight records upserted: {summary.weight_records_upserted}")
    print(f"Total Top-25 Traffic: {summary.total_basket_traffic:,}")
    print(f"Total Top-25 Share of Total Traffic: {summary.total_basket_share * 100:.2f}% ({summary.total_basket_share})")
    print(f"Total Basket Weight (raw sum): {summary.total_basket_weight_raw}")
    print(f"Total Basket Weight (8-decimal sum): {summary.total_basket_weight_rounded}")
    print("\nTop 5 Routes:")
    for item in summary.items[:5]:
        print(f"  {item.rank}. {item.city_1} - {item.city_2} (Route {item.route_code}, ID {item.route_id}): "
              f"Traffic = {item.total_route_traffic:,}, Weight = {item.basket_weight:.8f}, Share = {item.share_of_total_traffic:.4%}")
