"""Database-backed API and the small static jobs UI."""
import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID

from dotenv import set_key
from typing import List, Literal, Optional

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .models import JobListing
from .ai import settings
from .ingestion import SYNC_LOCK, recover_interrupted
from .sync import start_sync

logger = logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(app):
    try:
        with db.connect() as conn:
            db.initialize(conn)
            if conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (SYNC_LOCK,)).fetchone()["acquired"]:
                recover_interrupted(conn)
    except (psycopg.Error, RuntimeError):
        logger.warning("Database is unavailable at startup; API requests will retry.")
    yield


app = FastAPI(title="InternshipFeed", version="0.3.0", lifespan=lifespan)


def connection():
    try:
        with db.connect() as conn:
            yield conn
    except (psycopg.Error, RuntimeError):
        logger.exception("Database request failed")
        raise HTTPException(status_code=503, detail="Job storage is unavailable. Please try again later.")


@app.get("/api/jobs", response_model=List[JobListing])
def jobs(status: Optional[Literal["open", "closed", "unknown"]] = None,
         q: Optional[str] = Query(default=None, max_length=200), conn=Depends(connection)):
    return db.list_jobs(conn, status=status, query=q)


@app.get("/api/jobs/{job_id}", response_model=JobListing)
def job(job_id: int, conn=Depends(connection)):
    result = db.get_job(conn, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@app.get("/api/ingestion-runs/latest")
def ingestion_run(conn=Depends(connection)):
    return db.latest_run(conn)


def local_mutation(request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "This action is only available on the local development app.")
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if origin and origin != expected:
        raise HTTPException(403, "Cross-origin actions are not allowed.")


@app.post("/api/sync", status_code=202)
def sync_jobs(request: Request):
    local_mutation(request)
    try:
        return start_sync()
    except (psycopg.Error, RuntimeError):
        logger.exception("Could not start sync")
        raise HTTPException(503, "Unable to start sync. Please try again later.")


@app.get("/api/ingestion-runs/{run_id}")
def sync_progress(run_id: UUID, conn=Depends(connection)):
    report = db.get_run(conn, run_id)
    if report is None:
        raise HTTPException(404, "Sync not found")
    return report


@app.get("/api/settings/ai")
def ai_settings():
    return settings()


@app.post("/api/settings/ai")
async def save_ai_settings(request: Request, conn=Depends(connection)):
    local_mutation(request)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(422, "Provide a Google AI Studio API key.")
    key = payload.get("api_key") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not 20 <= len(key.strip()) <= 500:
        raise HTTPException(422, "Provide a valid Google AI Studio API key.")
    if payload.get("free_tier_confirmed") is not True:
        raise HTTPException(422, "Use a Google project on the Free tier with billing disabled.")
    if not conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (SYNC_LOCK,)).fetchone()["acquired"]:
        raise HTTPException(409, "Wait for the current sync to finish before changing AI settings.")
    try:
        set_key(str(db.ROOT / ".env"), "GEMINI_API_KEY", key.strip())
        os.environ["GEMINI_API_KEY"] = key.strip()
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK,))
    return settings()


@app.get("/api/health")
def health(conn=Depends(connection)):
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(db.ROOT / "index.html")


# Serve only UI assets; snapshots, .env, and data exports are not public routes.
app.mount("/web", StaticFiles(directory=db.ROOT / "web"), name="web")
