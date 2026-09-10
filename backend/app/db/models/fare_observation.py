from datetime import datetime, date, time
from decimal import Decimal
from typing import Optional, TYPE_CHECKING
# pyrefly: ignore [missing-import]
from sqlalchemy import (
    String,
    Boolean,
    SmallInteger,
    Integer,
    BigInteger,
    Numeric,
    Date,
    Time,
    DateTime,
    ForeignKey,
    Identity,
    CheckConstraint,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.collection_run import CollectionRun
    from backend.app.db.models.data_source import DataSource
    from backend.app.db.models.route import Route
    from backend.app.db.models.airline import Airline
    from backend.app.db.models.booking_window import BookingWindow


class FareObservation(Base):
    __tablename__ = "fare_observations"
    __table_args__ = (
        CheckConstraint("total_fare > 0", name="ck_fare_obs_total_fare_positive"),
        # Intra-run and source duplicate prevention
        UniqueConstraint(
            "run_id",
            "data_source_id",
            "fingerprint_hash",
            name="uq_fare_obs_run_source_fingerprint",
        ),
        Index(
            "idx_obs_route_window_travel",
            "route_id",
            "window_id",
            "travel_date",
            "quality_status",
        ),
        Index("idx_obs_fingerprint", "fingerprint_hash"),
        Index("idx_obs_observed_at", "observed_at"),
    )

    observation_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("collection_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    data_source_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("data_sources.source_id", ondelete="RESTRICT"),
        nullable=False,
    )
    route_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("routes.route_id", ondelete="RESTRICT"),
        nullable=False,
    )
    airline_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("airlines.airline_id", ondelete="RESTRICT"),
        nullable=False,
    )
    window_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("booking_windows.window_id", ondelete="RESTRICT"),
        nullable=False,
    )

    flight_number: Mapped[str] = mapped_column(String(15), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    travel_date: Mapped[date] = mapped_column(Date, nullable=False)
    advance_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Scraper-tolerant nullable fields
    scheduled_departure_time: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True
    )
    scheduled_arrival_time: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True
    )
    origin_airport_code: Mapped[Optional[str]] = mapped_column(
        String(3), nullable=True
    )
    destination_airport_code: Mapped[Optional[str]] = mapped_column(
        String(3), nullable=True
    )
    cabin_class: Mapped[str] = mapped_column(
        String(20), default="ECONOMY", nullable=False
    )
    fare_family: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_non_stop: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stops: Mapped[Optional[int]] = mapped_column(
        SmallInteger, default=0, nullable=True
    )

    total_fare: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    base_fare: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    fuel_surcharge: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    taxes_and_fees: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    convenience_fee: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", nullable=False
    )
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    seats_remaining: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True
    )

    quality_status: Mapped[str] = mapped_column(
        String(20), default="VALID", nullable=False
    )
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    collection_run: Mapped["CollectionRun"] = relationship(
        back_populates="fare_observations"
    )
    data_source: Mapped["DataSource"] = relationship(
        back_populates="fare_observations"
    )
    route: Mapped["Route"] = relationship(back_populates="fare_observations")
    airline: Mapped["Airline"] = relationship(back_populates="fare_observations")
    booking_window: Mapped["BookingWindow"] = relationship(
        back_populates="fare_observations"
    )
