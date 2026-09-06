from typing import List, TYPE_CHECKING
from sqlalchemy import String, Boolean, SmallInteger, Identity
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.collection_run import CollectionRun
    from backend.app.db.models.fare_observation import FareObservation
    from backend.app.db.models.base_period_fare import BasePeriodFare
    from backend.app.db.models.route_daily_summary import RouteDailySummary
    from backend.app.db.models.index_daily import IndexDaily


class BookingWindow(Base):
    __tablename__ = "booking_windows"

    window_id: Mapped[int] = mapped_column(
        SmallInteger, Identity(always=True), primary_key=True
    )
    window_code: Mapped[str] = mapped_column(
        String(10), unique=True, nullable=False
    )
    target_advance_days: Mapped[int] = mapped_column(
        SmallInteger, unique=True, nullable=False
    )
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    collection_runs: Mapped[List["CollectionRun"]] = relationship(
        back_populates="booking_window"
    )
    fare_observations: Mapped[List["FareObservation"]] = relationship(
        back_populates="booking_window"
    )
    base_fares: Mapped[List["BasePeriodFare"]] = relationship(
        back_populates="booking_window"
    )
    daily_summaries: Mapped[List["RouteDailySummary"]] = relationship(
        back_populates="booking_window"
    )
    index_records: Mapped[List["IndexDaily"]] = relationship(
        back_populates="booking_window"
    )
