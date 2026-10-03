"""Database-backed API and the small static jobs UI."""
import logging
from typing import List, Literal, Optional

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .models import JobListing

logger = logging.getLogger(__name__)
app = FastAPI(title="InternshipFeed", version="0.2.0")


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


@app.get("/api/health")
def health(conn=Depends(connection)):
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(db.ROOT / "index.html")


# Serve only UI assets; snapshots, .env, and data exports are not public routes.
app.mount("/web", StaticFiles(directory=db.ROOT / "web"), name="web")
