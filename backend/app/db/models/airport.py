from typing import TYPE_CHECKING
from sqlalchemy import String, Boolean, SmallInteger, ForeignKey, Identity, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.city import City


class Airport(Base):
    __tablename__ = "airports"

    airport_id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    iata_code: Mapped[str] = mapped_column(
        String(3), unique=True, nullable=False, index=True
    )
    city_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("cities.city_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    airport_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    city: Mapped["City"] = relationship(back_populates="airports")
