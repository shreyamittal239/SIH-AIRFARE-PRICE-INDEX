from datetime import datetime, date
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
    DateTime,
    ForeignKey,
    Identity,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.route import Route
    from backend.app.db.models.booking_window import BookingWindow


class RouteDailySummary(Base):
    __tablename__ = "route_daily_summary"
    __table_args__ = (
        UniqueConstraint(
            "observation_date",
            "route_id",
            "window_id",
            name="uq_route_daily_summary_obs_route_window",
        ),
        Index("idx_summary_lookup", "observation_date", "route_id", "window_id"),
    )

    summary_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
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
    target_travel_date: Mapped[date] = mapped_column(Date, nullable=False)
    observations_count: Mapped[int] = mapped_column(Integer, nullable=False)
    min_fare: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    median_fare: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    mean_fare: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    geometric_mean_fare: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    representative_fare: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    is_imputed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    imputation_method: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    route: Mapped["Route"] = relationship(back_populates="daily_summaries")
    booking_window: Mapped["BookingWindow"] = relationship(
        back_populates="daily_summaries"
    )
