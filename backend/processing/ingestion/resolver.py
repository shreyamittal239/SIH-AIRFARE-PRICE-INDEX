"""Database foreign key dimension resolver.

Resolves external flight quotation dimensions (airline, data source, route,
and advance booking window) to database foreign keys without hardcoding IDs.
"""

from dataclasses import dataclass
import logging
from typing import Optional
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from backend.app.db.models.airline import Airline
from backend.app.db.models.data_source import DataSource
from backend.app.db.models.route import Route
from backend.app.db.models.city import City
from backend.app.db.models.booking_window import BookingWindow
from backend.collectors.playwright.schemas.flight_quote import FlightQuote

logger = logging.getLogger(__name__)


# Known airline mapping for code normalization
AIRLINE_CODE_MAP = {
    "SPICEJET": ("SG", "SpiceJet"),
    "INDIGO": ("6E", "IndiGo"),
    "AIR INDIA": ("AI", "Air India"),
    "AKASA AIR": ("QP", "Akasa Air"),
    "AIR INDIA EXPRESS": ("IX", "Air India Express"),
}

# Known city codes and metadata
CITY_METADATA = {
    "DEL": ("DEL", "Delhi", "Delhi", True),
    "BOM": ("BOM", "Mumbai", "Maharashtra", True),
    "BLR": ("BLR", "Bengaluru", "Karnataka", True),
    "MAA": ("MAA", "Chennai", "Tamil Nadu", True),
    "CCU": ("CCU", "Kolkata", "West Bengal", True),
    "HYD": ("HYD", "Hyderabad", "Telangana", True),
    "GOI": ("GOI", "Goa", "Goa", False),
}


@dataclass
class ResolvedDimensions:
    """Container for resolved database foreign key dimensions."""

    airline_id: int
    data_source_id: int
    route_id: int
    window_id: int
    advance_days: int


class DimensionResolver:
    """Resolves flight quote strings into validated database foreign keys."""

    def __init__(self, db: Session, auto_create_reference_data: bool = True) -> None:
        """Initialize resolver.

        Args:
            db: Active SQLAlchemy database session.
            auto_create_reference_data: If True, automatically creates missing master
                                        records (Airlines, DataSources, Cities, Routes)
                                        for development and tests.
        """
        self.db = db
        self.auto_create = auto_create_reference_data

    def resolve_airline(self, airline_name_or_code: str) -> Airline:
        """Find or create Airline record."""
        clean = airline_name_or_code.strip()
        clean_upper = clean.upper()

        code, official_name = AIRLINE_CODE_MAP.get(clean_upper, (clean_upper[:3], clean))

        stmt = select(Airline).where(
            or_(
                Airline.airline_code == code,
                Airline.airline_name.ilike(clean),
            )
        )
        airline = self.db.scalars(stmt).first()

        if airline is None:
            if not self.auto_create:
                raise ValueError(f"Airline '{airline_name_or_code}' not found in database.")
            logger.info("Auto-creating reference record for Airline: code=%s, name=%s", code, official_name)
            airline = Airline(airline_code=code, airline_name=official_name, is_active=True)
            self.db.add(airline)
            self.db.flush()

        return airline

    def resolve_data_source(self, source_name_or_code: str, source_type: str = "AIRLINE_DIRECT") -> DataSource:
        """Find or create DataSource record."""
        clean = source_name_or_code.strip()
        code = clean.upper().replace(" ", "_")

        stmt = select(DataSource).where(
            or_(
                DataSource.source_code == code,
                DataSource.source_name.ilike(clean),
            )
        )
        source = self.db.scalars(stmt).first()

        if code in {"YATRA", "MAKEMYTRIP", "EASEMYTRIP", "CLEARTRIP", "IXIGO", "GOIBIBO"} and source_type == "AIRLINE_DIRECT":
            source_type = "OTA"

        if source is None:
            if not self.auto_create:
                raise ValueError(f"DataSource '{source_name_or_code}' not found in database.")
            logger.info("Auto-creating reference record for DataSource: code=%s, name=%s, type=%s", code, clean, source_type)
            source = DataSource(
                source_code=code,
                source_name=clean,
                source_type=source_type,
                is_active=True,
            )
            self.db.add(source)
            self.db.flush()
        elif code in {"YATRA", "MAKEMYTRIP", "EASEMYTRIP", "CLEARTRIP", "IXIGO", "GOIBIBO"} and source.source_type == "AIRLINE_DIRECT":
            source.source_type = "OTA"
            self.db.flush()

        return source

    def _resolve_city(self, city_code: str) -> City:
        """Find or create City master record."""
        code = city_code.strip().upper()
        stmt = select(City).where(City.city_code == code)
        city = self.db.scalars(stmt).first()

        if city is None:
            if not self.auto_create:
                raise ValueError(f"City '{city_code}' not found in database.")
            c_code, c_name, c_state, is_metro = CITY_METADATA.get(
                code, (code, code, "India", False)
            )
            city = City(
                city_code=c_code,
                city_name=c_name,
                state_name=c_state,
                is_metro=is_metro,
                is_active=True,
            )
            self.db.add(city)
            self.db.flush()

        return city

    def resolve_route(self, origin: str, destination: str) -> Route:
        """Find or create directional Route record."""
        orig_code = origin.strip().upper()
        dest_code = destination.strip().upper()
        route_code = f"{orig_code}-{dest_code}"

        stmt = select(Route).where(Route.route_code == route_code)
        route = self.db.scalars(stmt).first()

        if route is None:
            if not self.auto_create:
                raise ValueError(f"Route '{route_code}' not found in database.")
            origin_city = self._resolve_city(orig_code)
            dest_city = self._resolve_city(dest_code)

            logger.info("Auto-creating reference record for Route: %s", route_code)
            route = Route(
                origin_city_id=origin_city.city_id,
                destination_city_id=dest_city.city_id,
                route_code=route_code,
                is_active=True,
            )
            self.db.add(route)
            self.db.flush()

        return route

    def resolve_booking_window(self, advance_days: int) -> BookingWindow:
        """Look up the closest or exact BookingWindow for the given advance days."""
        # Check exact target advance days match (e.g. 1, 7, 15, 30, 45)
        stmt = select(BookingWindow).where(
            BookingWindow.target_advance_days == advance_days,
            BookingWindow.is_active == True,
        )
        window = self.db.scalars(stmt).first()

        if window is None:
            # Match nearest standard window
            all_windows = self.db.scalars(
                select(BookingWindow)
                .where(BookingWindow.is_active == True)
                .order_by(BookingWindow.display_order)
            ).all()

            if not all_windows:
                raise ValueError("No active booking windows seeded in database.")

            # Pick closest target_advance_days
            window = min(
                all_windows,
                key=lambda w: abs(w.target_advance_days - advance_days),
            )
            logger.debug(
                "Advance days %d matched to nearest window %s (target=%d)",
                advance_days,
                window.window_code,
                window.target_advance_days,
            )

        return window

    def resolve_dimensions(self, quote: FlightQuote) -> ResolvedDimensions:
        """Resolve all dimensional foreign keys for a FlightQuote."""
        airline = self.resolve_airline(quote.airline)
        source = self.resolve_data_source(quote.source)
        route = self.resolve_route(quote.origin, quote.destination)

        # Compute actual advance days relative to observation date
        advance_days = max(0, (quote.travel_date - quote.observed_at.date()).days)
        window = self.resolve_booking_window(advance_days)

        return ResolvedDimensions(
            airline_id=airline.airline_id,
            data_source_id=source.source_id,
            route_id=route.route_id,
            window_id=window.window_id,
            advance_days=advance_days,
        )
