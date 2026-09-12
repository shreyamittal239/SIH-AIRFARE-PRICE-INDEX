from datetime import datetime, date
from decimal import Decimal
from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    String,
    SmallInteger,
    Integer,
    BigInteger,
    Numeric,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    CheckConstraint,
    Index,
    text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.route import Route
    from backend.app.db.models.booking_window import BookingWindow


class IndexDaily(Base):
    __tablename__ = "index_daily"
    __table_args__ = (
        CheckConstraint(
            "index_level IN ('NATIONAL_COMPOSITE', 'WINDOW_COMPOSITE', 'ROUTE_LEVEL')",
            name="ck_index_level_valid",
        ),
        # Expression-based unique index handling nullable route_id and window_id
        Index(
            "uq_index_daily_coalesce",
            "index_date",
            "base_period_code",
            "index_level",
            text("COALESCE(route_id, -1)"),
            text("COALESCE(window_id, -1)"),
            "formula_type",
            unique=True,
        ),
        Index(
            "idx_index_daily_query",
            "index_date",
            "index_level",
            "base_period_code",
        ),
    )

    index_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    index_date: Mapped[date] = mapped_column(Date, nullable=False)
    base_period_code: Mapped[str] = mapped_column(String(30), nullable=False)
    index_level: Mapped[str] = mapped_column(String(20), nullable=False)
    route_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("routes.route_id", ondelete="CASCADE"),
        nullable=True,
    )
    window_id: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        ForeignKey("booking_windows.window_id", ondelete="CASCADE"),
        nullable=True,
    )
    index_value: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    price_relative: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False
    )
    formula_type: Mapped[str] = mapped_column(String(30), nullable=False)
    routes_included: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    route: Mapped[Optional["Route"]] = relationship(
        back_populates="index_records"
    )
    booking_window: Mapped[Optional["BookingWindow"]] = relationship(
        back_populates="index_records"
    )
