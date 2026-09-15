from datetime import datetime
from typing import List, TYPE_CHECKING
from sqlalchemy import (
    String,
    Boolean,
    SmallInteger,
    Integer,
    ForeignKey,
    Identity,
    DateTime,
    UniqueConstraint,
    CheckConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.city import City
    from backend.app.db.models.dgca_traffic_data import DGCATrafficData
    from backend.app.db.models.route_weight import RouteWeight
    from backend.app.db.models.fare_observation import FareObservation
    from backend.app.db.models.base_period_fare import BasePeriodFare
    from backend.app.db.models.route_daily_summary import RouteDailySummary
    from backend.app.db.models.index_daily import IndexDaily
    from backend.app.db.models.dgca_fare_benchmark import DGCAFareBenchmark



class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (
        UniqueConstraint(
            "origin_city_id", "destination_city_id", name="uq_routes_origin_dest"
        ),
        CheckConstraint(
            "origin_city_id <> destination_city_id",
            name="ck_routes_origin_dest_different",
        ),
    )

    route_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    origin_city_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("cities.city_id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination_city_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("cities.city_id", ondelete="RESTRICT"),
        nullable=False,
    )
    route_code: Mapped[str] = mapped_column(
        String(25), unique=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    origin_city: Mapped["City"] = relationship(
        foreign_keys=[origin_city_id], back_populates="origin_routes"
    )
    destination_city: Mapped["City"] = relationship(
        foreign_keys=[destination_city_id], back_populates="destination_routes"
    )
    dgca_traffic: Mapped[List["DGCATrafficData"]] = relationship(
        back_populates="route"
    )
    weights: Mapped[List["RouteWeight"]] = relationship(back_populates="route")
    fare_observations: Mapped[List["FareObservation"]] = relationship(
        back_populates="route"
    )
    base_fares: Mapped[List["BasePeriodFare"]] = relationship(
        back_populates="route"
    )
    daily_summaries: Mapped[List["RouteDailySummary"]] = relationship(
        back_populates="route"
    )
    index_records: Mapped[List["IndexDaily"]] = relationship(
        back_populates="route"
    )
    dgca_benchmarks: Mapped[List["DGCAFareBenchmark"]] = relationship(
        back_populates="route"
    )

