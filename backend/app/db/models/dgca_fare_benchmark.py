from datetime import date
from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    String,
    Integer,
    Numeric,
    Date,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.route import Route


class DGCAFareBenchmark(Base):
    __tablename__ = "dgca_fare_benchmark"
    __table_args__ = (
        UniqueConstraint(
            "route_id",
            "period_start",
            "period_end",
            name="uq_dgca_fare_benchmark_route_period",
        ),
        Index("idx_dgca_fare_dates", "period_start", "period_end"),
    )

    benchmark_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    route_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("routes.route_id", ondelete="RESTRICT"),
        nullable=True,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    dgca_avg_fare: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    report_source: Mapped[Optional[str]] = mapped_column(
        String(150), nullable=True
    )

    # Relationships
    route: Mapped[Optional["Route"]] = relationship(back_populates="dgca_benchmarks")
