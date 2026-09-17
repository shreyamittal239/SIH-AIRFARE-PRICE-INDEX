"""Unit and integration tests for SchedulerLock (PostgreSQL advisory locking)."""

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.engine import Engine, Connection

from backend.scheduler.lock import SchedulerLock
from backend.app.db.database import engine as real_engine


class TestSchedulerLockUnit:
    """Mock-based unit tests for SchedulerLock mechanics."""

    def test_lock_acquire_success(self):
        """Test successful non-blocking lock acquisition."""
        mock_conn = MagicMock(spec=Connection)
        mock_conn.execute.return_value.scalar.return_value = True

        mock_engine = MagicMock(spec=Engine)
        mock_engine.connect.return_value = mock_conn

        lock = SchedulerLock(lock_id=9999, engine=mock_engine)
        assert not lock.is_acquired

        acquired = lock.acquire()
        assert acquired is True
        assert lock.is_acquired is True
        assert mock_conn.execute.called

    def test_lock_acquire_already_held_by_another_process(self, caplog):
        """Test second acquisition attempt fails and logs required warning message."""
        mock_conn = MagicMock(spec=Connection)
        # Advisory lock returns False because another session holds it
        mock_conn.execute.return_value.scalar.return_value = False

        mock_engine = MagicMock(spec=Engine)
        mock_engine.connect.return_value = mock_conn

        lock = SchedulerLock(lock_id=9999, engine=mock_engine)

        with caplog.at_level("WARNING"):
            acquired = lock.acquire()

        assert acquired is False
        assert lock.is_acquired is False
        # Verify connection was closed immediately
        assert mock_conn.close.called
        # Verify exact required warning message is logged
        assert "Collection cycle skipped because another cycle is already running." in caplog.text

    def test_lock_release(self):
        """Test clean release of advisory lock and connection closure."""
        mock_conn = MagicMock(spec=Connection)
        mock_conn.execute.return_value.scalar.return_value = True

        mock_engine = MagicMock(spec=Engine)
        mock_engine.connect.return_value = mock_conn

        lock = SchedulerLock(lock_id=9999, engine=mock_engine)
        lock.acquire()
        assert lock.is_acquired

        lock.release()
        assert not lock.is_acquired
        assert mock_conn.execute.call_count == 2  # 1 try_advisory_lock, 1 advisory_unlock
        assert mock_conn.close.called

    def test_lock_context_manager(self):
        """Test lock behavior within a context manager block."""
        mock_conn = MagicMock(spec=Connection)
        mock_conn.execute.return_value.scalar.return_value = True

        mock_engine = MagicMock(spec=Engine)
        mock_engine.connect.return_value = mock_conn

        lock = SchedulerLock(lock_id=9999, engine=mock_engine)
        with lock:
            assert lock.is_acquired

        assert not lock.is_acquired
        assert mock_conn.close.called

    def test_lock_release_on_exception_in_context_manager(self):
        """Test that an exception inside context manager still releases the lock."""
        mock_conn = MagicMock(spec=Connection)
        mock_conn.execute.return_value.scalar.return_value = True

        mock_engine = MagicMock(spec=Engine)
        mock_engine.connect.return_value = mock_conn

        lock = SchedulerLock(lock_id=9999, engine=mock_engine)
        with pytest.raises(RuntimeError):
            with lock:
                assert lock.is_acquired
                raise RuntimeError("Simulated crash inside collection run")

        assert not lock.is_acquired
        assert mock_conn.close.called

    def test_lock_acquire_exception_handled(self, caplog):
        """Test database connection exception during acquire is caught gracefully."""
        mock_engine = MagicMock(spec=Engine)
        mock_engine.connect.side_effect = Exception("DB Connection refused")

        lock = SchedulerLock(lock_id=9999, engine=mock_engine)
        with caplog.at_level("ERROR"):
            acquired = lock.acquire()

        assert acquired is False
        assert not lock.is_acquired
        assert "Error attempting to acquire advisory lock" in caplog.text


class TestSchedulerLockIntegration:
    """Live PostgreSQL advisory lock integration test using the active database engine."""

    def test_live_postgres_mutual_exclusion(self):
        """Test real PostgreSQL session-level advisory locking prevents overlap and releases cleanly."""
        test_lock_id = 987654321  # distinct key for testing

        lock1 = SchedulerLock(lock_id=test_lock_id, engine=real_engine)
        lock2 = SchedulerLock(lock_id=test_lock_id, engine=real_engine)

        try:
            # First lock should succeed
            assert lock1.acquire() is True
            assert lock1.is_acquired is True

            # Second lock on distinct session must fail immediately
            assert lock2.acquire() is False
            assert lock2.is_acquired is False

            # Release first lock
            lock1.release()
            assert lock1.is_acquired is False

            # Now second lock should succeed
            assert lock2.acquire() is True
            assert lock2.is_acquired is True

        finally:
            lock1.release()
            lock2.release()
