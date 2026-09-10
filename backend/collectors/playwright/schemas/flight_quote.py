"""Normalized flight quotation schema.

Represents an individual flight quote extracted from an airline or OTA portal
before downstream database ingestion and index calculation.
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class FlightQuote(BaseModel):
    """Normalized flight quotation model."""

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
    )

    airline: str = Field(description="Airline name or brand (e.g., 'SpiceJet')")
    flight_number: str = Field(description="Marketing or operating flight number (e.g., 'SG 162')")
    origin: str = Field(description="Origin 3-letter IATA airport code (e.g., 'DEL')")
    destination: str = Field(description="Destination 3-letter IATA airport code (e.g., 'BOM')")
    travel_date: date = Field(description="Flight departure date")

    departure_time: Optional[time] = Field(
        default=None, description="Scheduled local departure time"
    )
    arrival_time: Optional[time] = Field(
        default=None, description="Scheduled local arrival time"
    )
    cabin_class: str = Field(
        default="ECONOMY", description="Travel cabin class (e.g., 'ECONOMY')"
    )
    fare_family: Optional[str] = Field(
        default=None, description="Fare category/bundle (e.g., 'SpiceSaver', 'SpiceMax')"
    )
    stops: Optional[int] = Field(
        default=0, description="Number of intermediate stops (0 for non-stop/direct)"
    )

    total_fare: Decimal = Field(
        description="Total quoted price inclusive of base fare, taxes, and fees"
    )
    currency: str = Field(
        default="INR", description="Currency code (e.g., 'INR')"
    )
    availability: bool = Field(
        default=True, description="Whether the flight is available for booking"
    )
    baggage: Optional[str] = Field(
        default=None, description="Included check-in/cabin baggage allowance if stated"
    )
    source: str = Field(
        default="Airline Direct", description="Data source name (e.g., 'SpiceJet Direct')"
    )
    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when quotation was observed (UTC)",
    )
