from typing import List, Dict, Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.db.database import SessionLocal
from backend.app.db.models.booking_window import BookingWindow

SIH_BOOKING_WINDOWS: List[Dict[str, Any]] = [
    {"window_code": "T+1", "target_advance_days": 1, "display_order": 1, "is_active": True},
    {"window_code": "T+7", "target_advance_days": 7, "display_order": 2, "is_active": True},
    {"window_code": "T+15", "target_advance_days": 15, "display_order": 3, "is_active": True},
    {"window_code": "T+30", "target_advance_days": 30, "display_order": 4, "is_active": True},
    {"window_code": "T+45", "target_advance_days": 45, "display_order": 5, "is_active": True},
]


def seed_booking_windows(db: Session) -> int:
    """
    Idempotently seed the five SIH booking windows:
    T+1, T+7, T+15, T+30, T+45.
    Returns the count of newly inserted rows.
    """
    inserted_count = 0
    for window_data in SIH_BOOKING_WINDOWS:
        stmt = select(BookingWindow).where(
            BookingWindow.window_code == window_data["window_code"]
        )
        existing = db.scalars(stmt).first()
        if existing is None:
            new_window = BookingWindow(**window_data)
            db.add(new_window)
            inserted_count += 1

    if inserted_count > 0:
        db.commit()

    return inserted_count


if __name__ == "__main__":
    with SessionLocal() as session:
        count = seed_booking_windows(session)
        print(f"Seeded {count} new booking windows.")
        stmt = select(BookingWindow).order_by(BookingWindow.display_order)
        windows = session.scalars(stmt).all()
        for w in windows:
            print(f" - {w.window_code}: target {w.target_advance_days} days (order: {w.display_order})")
