from typing import List, TYPE_CHECKING
from sqlalchemy import String, Boolean, SmallInteger, Identity
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.collection_run import CollectionRun
    from backend.app.db.models.fare_observation import FareObservation


class DataSource(Base):
    __tablename__ = "data_sources"

    source_id: Mapped[int] = mapped_column(
        SmallInteger, Identity(always=True), primary_key=True
    )
    source_code: Mapped[str] = mapped_column(
        String(30), unique=True, nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    collection_runs: Mapped[List["CollectionRun"]] = relationship(
        back_populates="data_source"
    )
    fare_observations: Mapped[List["FareObservation"]] = relationship(
        back_populates="data_source"
    )
