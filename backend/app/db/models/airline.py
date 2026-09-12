from typing import List, Optional, Any, Dict, TYPE_CHECKING
# pyrefly: ignore [missing-import]
from sqlalchemy import String, Boolean, SmallInteger, Identity, JSON, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.fare_observation import FareObservation


class Airline(Base):
    __tablename__ = "airlines"

    airline_id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    airline_code: Mapped[str] = mapped_column(
        String(3), unique=True, nullable=False, index=True
    )
    airline_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Open JSONB metadata for future extensibility without migrations
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", JSON, nullable=True
    )

    # Relationships
    fare_observations: Mapped[List["FareObservation"]] = relationship(
        back_populates="airline"
    )
