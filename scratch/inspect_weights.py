from backend.app.db.database import SessionLocal
from backend.app.db.models.booking_window_weight import BookingWindowWeight
from sqlalchemy import select

with SessionLocal() as session:
    weights = session.scalars(select(BookingWindowWeight)).all()
    print(f"Total weights in DB: {len(weights)}")
    for w in weights:
        print(f"ID: {w.id}, WindowID: {w.window_id}, Active: {w.is_active}, Weight: {w.weight}")
