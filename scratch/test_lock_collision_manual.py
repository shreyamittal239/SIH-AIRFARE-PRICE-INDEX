"""Manual multi-process lock collision verification.

Verifies:
1. Process A acquires advisory lock 847291.
2. Process B attempts to run `python -m backend.scheduler.scheduler --run-now`.
3. Process B fails to acquire lock, logs:
   'Collection cycle skipped because another cycle is already running.'
4. Process B exits cleanly with code 0.
5. Process A releases the lock.
"""

import subprocess
import sys
import os

sys.path.insert(0, os.path.abspath("."))

import time
from backend.app.db.database import engine
from backend.scheduler.lock import SchedulerLock


def main():
    print("[TEST 3] Acquiring lock in parent process A...")
    lock = SchedulerLock(lock_id=847291, engine=engine)
    acquired = lock.acquire()
    assert acquired, "Process A failed to acquire lock!"
    print("[TEST 3] Lock acquired by Process A. Now spawning Process B (--run-now)...")

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "backend.scheduler.scheduler", "--run-now"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        print("[TEST 3] Process B finished with return code:", proc.returncode)
        print("[TEST 3] Process B stdout:\n", proc.stdout)
        print("[TEST 3] Process B stderr:\n", proc.stderr)

        combined_output = proc.stdout + proc.stderr
        expected_msg = "Collection cycle skipped because another cycle is already running."
        assert expected_msg in combined_output, f"Expected message '{expected_msg}' not found in output!"
        assert proc.returncode == 0, f"Process B returned non-zero code {proc.returncode}"
        print("[TEST 3] SUCCESS: Second cycle was cleanly rejected because lock is held!")
    finally:
        print("[TEST 3] Releasing lock in parent process A...")
        lock.release()
        print("[TEST 3] Lock released.")


if __name__ == "__main__":
    main()
