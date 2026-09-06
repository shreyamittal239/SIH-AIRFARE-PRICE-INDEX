from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    String,
    SmallInteger,
    Integer,
    Numeric,
    DateTime,
    ForeignKey,
    Identity,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.route import Route
    from backend.app.db.models.booking_window import BookingWindow


class BasePeriodFare(Base):
    __tablename__ = "base_period_fares"
    __table_args__ = (
        UniqueConstraint(
            "base_period_code",
            "route_id",
            "window_id",
            name="uq_base_fares_period_route_window",
        ),
    )

    base_fare_id: Mapped[int] = mapped_column(
        Integer, Identity(always=True), primary_key=True
    )
    base_period_code: Mapped[str] = mapped_column(String(30), nullable=False)
    route_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("routes.route_id", ondelete="RESTRICT"),
        nullable=False,
    )
    window_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("booking_windows.window_id", ondelete="RESTRICT"),
        nullable=False,
    )
    benchmark_fare: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    methodology_notes: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    route: Mapped["Route"] = relationship(back_populates="base_fares")
    booking_window: Mapped["BookingWindow"] = relationship(
        back_populates="base_fares"
    )
