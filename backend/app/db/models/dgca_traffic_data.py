from datetime import datetime, date
from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    String,
    Integer,
    BigInteger,
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


class DGCATrafficData(Base):
    __tablename__ = "dgca_traffic_data"
    __table_args__ = (
        UniqueConstraint(
            "route_id",
            "period_type",
            "period_start",
            "period_end",
            name="uq_dgca_traffic_route_period",
        ),
        Index("idx_dgca_route_dates", "route_id", "period_start", "period_end"),
    )

    traffic_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    route_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("routes.route_id", ondelete="RESTRICT"),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)
    passengers_carried: Mapped[int] = mapped_column(BigInteger, nullable=False)
    flights_operated: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    report_source: Mapped[str] = mapped_column(String(150), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    route: Mapped["Route"] = relationship(back_populates="dgca_traffic")
