from datetime import datetime
from typing import List, Optional, TYPE_CHECKING
from sqlalchemy import (
    String,
    SmallInteger,
    Integer,
    BigInteger,
    ForeignKey,
    Identity,
    DateTime,
    Text,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.data_source import DataSource
    from backend.app.db.models.route import Route
    from backend.app.db.models.booking_window import BookingWindow
    from backend.app.db.models.fare_observation import FareObservation


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (
        Index("idx_runs_started_status", "started_at", "status"),
    )

    run_id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    source_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("data_sources.source_id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_route_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("routes.route_id", ondelete="SET NULL"),
        nullable=True,
    )
    target_window_id: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        ForeignKey("booking_windows.window_id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="RUNNING", nullable=False
    )
    raw_artifact_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    records_scraped: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    data_source: Mapped["DataSource"] = relationship(
        back_populates="collection_runs"
    )
    target_route: Mapped[Optional["Route"]] = relationship()
    booking_window: Mapped[Optional["BookingWindow"]] = relationship(
        back_populates="collection_runs"
    )
    fare_observations: Mapped[List["FareObservation"]] = relationship(
        back_populates="collection_run", cascade="all, delete-orphan"
    )
