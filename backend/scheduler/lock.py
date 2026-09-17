"""Process-level mutual exclusion lock for the scraping scheduler.

Uses PostgreSQL session-level advisory locks (pg_try_advisory_lock) as the primary mechanism.
Ensures that:
1. Overlapping collection cycles are strictly prevented.
2. If another collection cycle is active, subsequent invocations immediately log:
   'Collection cycle skipped because another cycle is already running.'
   and return cleanly.
3. If the process terminates or crashes unexpectedly, PostgreSQL automatically releases
   the session-level advisory lock upon connection termination.
4. Clean context manager (__enter__, __exit__) and acquire/release semantics.
"""

import logging
from typing import Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine, Connection

from backend.app.db.database import engine as default_engine

logger = logging.getLogger(__name__)


class SchedulerLock:
    """PostgreSQL session-level advisory lock for scheduler mutual exclusion."""

    def __init__(
        self,
        lock_id: int = 847291,
        engine: Optional[Engine] = None,
    ) -> None:
        """Initialize scheduler lock.

        Args:
            lock_id: 64-bit integer identifier for PostgreSQL advisory lock.
            engine: SQLAlchemy Engine instance (defaults to backend.app.db.database.engine).
        """
        self.lock_id = lock_id
        self.engine = engine or default_engine
        self._connection: Optional[Connection] = None
        self._is_acquired: bool = False

    @property
    def is_acquired(self) -> bool:
        """Whether this lock instance is currently holding the advisory lock."""
        return self._is_acquired

    def acquire(self) -> bool:
        """Attempt to acquire the advisory lock non-blockingly.

        Returns:
            True if lock was successfully acquired; False if already held by another cycle.
        """
        if self._is_acquired:
            logger.debug("SchedulerLock %d is already acquired by this instance.", self.lock_id)
            return True

        try:
            conn = self.engine.connect()
            # Non-blocking advisory lock attempt
            result = conn.execute(
                text("SELECT pg_try_advisory_lock(:id)"),
                {"id": self.lock_id},
            ).scalar()

            if result:
                self._connection = conn
                self._is_acquired = True
                logger.info("[LOCK] Successfully acquired scheduler advisory lock (%d).", self.lock_id)
                return True
            else:
                conn.close()
                self._connection = None
                self._is_acquired = False
                logger.warning("Collection cycle skipped because another cycle is already running.")
                return False

        except Exception as exc:
            logger.error("[LOCK] Error attempting to acquire advisory lock (%d): %s", self.lock_id, exc)
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass
                self._connection = None
            self._is_acquired = False
            return False

    def release(self) -> None:
        """Release the advisory lock and close the underlying connection."""
        if not self._is_acquired or self._connection is None:
            self._is_acquired = False
            self._connection = None
            return

        try:
            self._connection.execute(
                text("SELECT pg_advisory_unlock(:id)"),
                {"id": self.lock_id},
            )
            logger.info("[LOCK] Successfully released scheduler advisory lock (%d).", self.lock_id)
        except Exception as exc:
            logger.warning("[LOCK] Notice while releasing advisory lock (%d): %s", self.lock_id, exc)
        finally:
            try:
                self._connection.close()
            except Exception as close_err:
                logger.debug("[LOCK] Error closing lock connection: %s", close_err)
            self._connection = None
            self._is_acquired = False

    def __enter__(self) -> "SchedulerLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
