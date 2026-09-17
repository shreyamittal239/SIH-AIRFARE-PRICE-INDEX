"""Step 2: Safely update interrupted Run #328 in PostgreSQL.

Preserves the audit trail by recording that Run #328 was interrupted by host sleep
with 0 persisted observations before completion.
"""
from datetime import datetime, timezone
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from backend.app.db.database import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("step2_handle_run_328")

def main():
    session = SessionLocal()
    try:
        # Check current state of Run 328
        stmt = text("""
            SELECT run_id, status, records_scraped, started_at, completed_at, error_summary,
                   (SELECT COUNT(*) FROM fare_observations WHERE run_id = 328) AS obs_count
            FROM collection_runs
            WHERE run_id = 328;
        """)
        row = session.execute(stmt).mappings().one_or_none()
        if not row:
            logger.error("Run 328 not found in database!")
            return

        logger.info("Current Run 328 state: status=%s, records_scraped=%s, obs_count=%s",
                    row["status"], row["records_scraped"], row["obs_count"])

        if row["status"] == "RUNNING" and row["obs_count"] == 0:
            # Mark as FAILED due to interruption, preserving audit trail
            interrupted_completed_at = datetime(2026, 9, 15, 11, 34, 49, 228000, tzinfo=timezone.utc)
            error_msg = "Interrupted: host machine entered sleep state before database persistence transaction completed."
            
            update_stmt = text("""
                UPDATE collection_runs
                SET status = 'FAILED',
                    completed_at = :completed_at,
                    records_scraped = 0,
                    error_summary = :error_summary
                WHERE run_id = 328;
            """)
            session.execute(update_stmt, {
                "completed_at": interrupted_completed_at,
                "error_summary": error_msg,
            })
            session.commit()
            logger.info("Successfully updated Run 328 to status='FAILED' with interruption note.")
        else:
            logger.info("Run 328 is already in status=%s (no update needed).", row["status"])

        # Verify updated row
        verified = session.execute(stmt).mappings().one()
        logger.info("Verified Run 328 state: status=%s, completed_at=%s, error_summary=%s",
                    verified["status"], verified["completed_at"], verified["error_summary"])

    finally:
        session.close()

if __name__ == "__main__":
    main()
