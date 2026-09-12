from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from sqlalchemy import (
    String,
    Boolean,
    Integer,
    BigInteger,
    Numeric,
    DateTime,
    ForeignKey,
    Identity,
    UniqueConstraint,
    CheckConstraint,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.route import Route


class RouteWeight(Base):
    __tablename__ = "route_weights"
    __table_args__ = (
        UniqueConstraint(
            "route_id", "base_period_code", name="uq_route_weights_route_base"
        ),
        CheckConstraint(
            "weight >= 0 AND weight <= 1", name="ck_route_weight_range"
        ),
        Index("idx_weights_active", "base_period_code", "is_active"),
    )

    weight_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    route_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("routes.route_id", ondelete="RESTRICT"),
        nullable=False,
    )
    base_period_code: Mapped[str] = mapped_column(String(30), nullable=False)
    passenger_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(10, 8), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    route: Mapped["Route"] = relationship(back_populates="weights")
