"""Asynchronous UI sync guarded by a database session lock."""
import threading
from . import db
from .ingestion import SYNC_LOCK, new_run, recover_interrupted, run_ingestion


def start_sync():
    conn = db.connect()
    try:
        db.initialize(conn)
        acquired = conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (SYNC_LOCK,)).fetchone()["acquired"]
        if not acquired:
            latest = db.latest_run(conn)
            conn.close()
            return latest
        recover_interrupted(conn)
        run = new_run()
        db.record_run(conn, run)
        threading.Thread(target=run_ingestion, kwargs={"conn": conn, "run": run.copy(), "owns_lock": True}, daemon=True, name="internship-sync").start()
        return run
    except Exception:
        conn.close()
        raise
