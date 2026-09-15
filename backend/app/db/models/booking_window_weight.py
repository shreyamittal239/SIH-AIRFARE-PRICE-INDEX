from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    String,
    Boolean,
    Integer,
    Numeric,
    ForeignKey,
    CheckConstraint,
    Index,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.booking_window import BookingWindow


class BookingWindowWeight(Base):
    __tablename__ = "booking_window_weights"
    __table_args__ = (
        CheckConstraint(
            "weight >= 0 AND weight <= 1",
            name="ck_window_weight_range"
        ),
        # Only one active weight per window at a time
        Index(
            "uq_active_window_weight",
            "window_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    window_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("booking_windows.window_id", ondelete="RESTRICT"),
        nullable=False,
    )
    weight: Mapped[float] = mapped_column(
        Numeric(6, 4), nullable=False
    )
    source_note: Mapped[Optional[str]] = mapped_column(
        String(150), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    # Relationships
    booking_window: Mapped["BookingWindow"] = relationship(
        back_populates="window_weights"
    )
