from typing import List, TYPE_CHECKING
from sqlalchemy import String, Boolean, SmallInteger, Identity
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.airport import Airport
    from backend.app.db.models.route import Route


class City(Base):
    __tablename__ = "cities"

    city_id: Mapped[int] = mapped_column(
        SmallInteger, Identity(always=True), primary_key=True
    )
    city_code: Mapped[str] = mapped_column(
        String(10), unique=True, nullable=False, index=True
    )
    city_name: Mapped[str] = mapped_column(String(80), nullable=False)
    state_name: Mapped[str] = mapped_column(String(60), nullable=False)
    is_metro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    airports: Mapped[List["Airport"]] = relationship(
        back_populates="city", cascade="all, delete-orphan"
    )
    origin_routes: Mapped[List["Route"]] = relationship(
        foreign_keys="Route.origin_city_id", back_populates="origin_city"
    )
    destination_routes: Mapped[List["Route"]] = relationship(
        foreign_keys="Route.destination_city_id", back_populates="destination_city"
    )
