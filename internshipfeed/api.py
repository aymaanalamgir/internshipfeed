"""Database-backed API and the consolidated React product."""
import logging
from contextlib import asynccontextmanager
from uuid import UUID

from typing import List, Literal, Optional

import psycopg
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .models import (CandidateJobView, CandidateProfile, CandidateProfileUpdate, CanonicalJob,
                     InteractionReceipt, InteractionRequest, JobDetails, JobInteractionState, RecommendedJobView, Resume, ResumeMetadata, ResumeSkillSuggestions)
from .matching import score_job
from .behavioral import build_behavior_profile
from .recommendations import rank_recommendations
from .resume_skills import extract_resume_skills, skill_aliases
from .resumes import MAX_UPLOAD_BYTES, ResumeExtractionError, extract_pdf_text
from .ingestion import SYNC_LOCK, recover_interrupted
from .sync import start_sync

logger = logging.getLogger(__name__)
FRONTEND_ENTRY = db.ROOT / 'frontend' / 'dist' / 'index.html'
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


@app.get("/api/sources")
def sources(conn=Depends(connection)):
    return db.source_directory(conn)


@app.get("/api/jobs", response_model=List[CanonicalJob])
def jobs(status: Optional[Literal["open", "closed", "unknown"]] = None,
         q: Optional[str] = Query(default=None, max_length=200),
         source: Optional[str] = None, source_id: Optional[int] = None, conn=Depends(connection)):
    profile = _development_candidate(conn)
    return [details.job for details, _ in db.candidate_job_details(conn, profile['id'], status=status, query=q, source=source, source_id=source_id)]


@app.get("/api/feed", response_model=List[JobDetails])
def feed(status: Optional[Literal["open", "closed", "unknown"]] = None,
         q: Optional[str] = Query(default=None, max_length=200),
         source: Optional[str] = None, source_id: Optional[int] = None, conn=Depends(connection)):
    profile = _development_candidate(conn)
    return [details for details, _ in db.candidate_job_details(conn, profile['id'], status=status, query=q, source=source, source_id=source_id)]


@app.get("/api/jobs/{job_id}/details", response_model=JobDetails)
def job_details(job_id: int, conn=Depends(connection)):
    result = db.get_job_details(conn, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@app.get("/api/jobs/{job_id}", response_model=CanonicalJob)
def job(job_id: int, conn=Depends(connection)):
    result = db.get_job(conn, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@app.get("/api/ingestion-batches/latest")
def latest_batch(conn=Depends(connection)):
    return db.latest_batch(conn)


@app.get("/api/ingestion-runs")
def ingestion_runs(source_id: Optional[int] = None, conn=Depends(connection)):
    return db.list_ingestion_runs(conn, source_id=source_id)


@app.get("/api/ingestion-runs/latest")
def latest_ingestion_run(conn=Depends(connection)):
    rows = db.list_ingestion_runs(conn)
    return rows[0] if rows else None


def local_mutation(request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "This action is only available on the local development app.")
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if origin and origin != expected:
        raise HTTPException(403, "Cross-origin actions are not allowed.")


@app.get("/api/profile", response_model=CandidateProfile)
def profile(conn=Depends(connection)):
    result = db.development_profile(conn)
    if result is None:
        raise HTTPException(503, "Development profile is missing. Initialize the database before using profiles.")
    return result


def _development_candidate(conn):
    profile = db.development_profile(conn)
    if profile is None:
        raise HTTPException(503, "Development profile is missing. Initialize the database before using job views.")
    return profile


def _job_collection(conn, view, min_score=0):
    profile = CandidateProfile.model_validate(_development_candidate(conn))
    entries = db.candidate_job_details(conn, profile.id, view)
    details, states = [item for item, _ in entries], {item.job.id: state for item, state in entries}
    if view == 'recommended':
        behavior = build_behavior_profile(db.behavior_history(conn, profile.id))
        matches = rank_recommendations(profile, details, behavior, min_score=min_score)
    else:
        matches = [score_job(profile, item) for item in details]
    if view != 'recommended':
        field = 'saved_at' if view == 'saved' else 'applied_at'
        matches.sort(key=lambda match: (getattr(states[match.job.id], field).timestamp(), match.job.id), reverse=True)
    result_model = RecommendedJobView if view == 'recommended' else CandidateJobView
    metadata = {item.job.id: item for item in details}
    return [result_model(**match.model_dump(), interaction_state=states[match.job.id],
                         term=metadata[match.job.id].term, keywords=metadata[match.job.id].keywords) for match in matches]


@app.get("/api/recommendations", response_model=List[RecommendedJobView])
def recommendations(min_score: int = Query(default=0, ge=0, le=100), conn=Depends(connection)):
    return _job_collection(conn, 'recommended', min_score)


@app.get("/api/saved", response_model=List[CandidateJobView])
def saved_jobs(conn=Depends(connection)):
    return _job_collection(conn, 'saved')


@app.get("/api/applied", response_model=List[CandidateJobView])
def applied_jobs(conn=Depends(connection)):
    return _job_collection(conn, 'applied')


@app.put("/api/profile", response_model=CandidateProfile)
def update_profile(update: CandidateProfileUpdate, request: Request, conn=Depends(connection)):
    local_mutation(request)
    result = db.update_development_profile(conn, update)
    if result is None:
        raise HTTPException(503, "Development profile is missing. Initialize the database before using profiles.")
    return result


@app.get("/api/resumes/latest", response_model=Optional[ResumeMetadata])
def latest_resume(conn=Depends(connection)):
    return db.latest_resume_metadata(conn, _development_candidate(conn)['id'])


@app.get("/api/resumes/{resume_id}/skill-suggestions", response_model=ResumeSkillSuggestions)
def resume_skill_suggestions(resume_id: int, conn=Depends(connection)):
    candidate = _development_candidate(conn)
    resume = conn.execute('SELECT id, extracted_text FROM resumes WHERE id=%s AND candidate_profile_id=%s',
                          (resume_id, candidate['id'])).fetchone()
    if resume is None:
        raise HTTPException(404, "Resume not found")
    try:
        return {'resume_id': resume['id'], 'skills': extract_resume_skills(resume['extracted_text']), 'aliases': skill_aliases()}
    except Exception:
        # Never include stored résumé text in logs or the error response.
        logger.warning('Resume skill suggestions unavailable')
        raise HTTPException(503, 'Skill suggestions are unavailable. Please try again.') from None


@app.get("/api/interactions", response_model=List[JobInteractionState])
def interactions(conn=Depends(connection)):
    candidate = db.development_profile(conn)
    if candidate is None:
        raise HTTPException(503, "Development profile is missing. Initialize the database before using interactions.")
    return db.interaction_states(conn, candidate['id'])


@app.post("/api/jobs/{job_id}/interactions", response_model=InteractionReceipt, status_code=201)
def job_interaction(job_id: int, interaction: InteractionRequest, request: Request, conn=Depends(connection)):
    local_mutation(request)
    candidate = db.development_profile(conn)
    if candidate is None:
        raise HTTPException(503, "Development profile is missing. Initialize the database before using interactions.")
    try:
        return db.record_interaction_with_state(conn, candidate['id'], job_id, interaction.type)
    except ValueError:
        raise HTTPException(404, "Job not found") from None


@app.post("/api/sync", status_code=202)
def sync_jobs(request: Request):
    local_mutation(request)
    try:
        return start_sync()
    except (psycopg.Error, RuntimeError):
        logger.exception("Could not start sync")
        raise HTTPException(503, "Unable to start sync. Please try again later.")


@app.post("/api/resumes", response_model=Resume, status_code=201)
def upload_resume(request: Request, file: UploadFile = File(...), conn=Depends(connection)):
    try:
        local_mutation(request)
        # Retain a filename, never a path supplied by a client on either platform.
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename.lower().endswith(".pdf") or "\x00" in filename:
            raise HTTPException(415, "Only .pdf resumes are supported.")
        mime = (file.content_type or "").split(";", 1)[0].strip().lower()
        if mime not in {"application/pdf", "application/octet-stream", ""}:
            raise HTTPException(415, "Upload a PDF with content type application/pdf.")
        if file.size is not None and file.size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "PDF must be 5 MiB or smaller.")
        # UploadFile spools larger files; this read also bounds memory when size is absent.
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "PDF must be 5 MiB or smaller.")
        candidate = db.development_profile(conn)
        if candidate is None:
            raise HTTPException(503, "Development profile is missing. Initialize the database before using resumes.")
        try:
            text = extract_pdf_text(content)
        except ResumeExtractionError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            return db.save_resume_text(conn, candidate["id"], filename, text)
        except psycopg.Error:
            # PostgreSQL diagnostics may include rejected row text. Do not log them.
            logger.warning("Resume storage request failed")
            raise HTTPException(503, "Resume storage is unavailable. Please try again later.") from None
    finally:
        file.file.close()


@app.get("/api/ingestion-batches/{run_id}")
def sync_progress(run_id: UUID, conn=Depends(connection)):
    report = db.get_batch(conn, run_id)
    if report is None:
        raise HTTPException(404, "Sync not found")
    return report


@app.get("/api/ingestion-runs/{run_id}")
def ingestion_run(run_id: UUID, conn=Depends(connection)):
    report = db.get_ingestion_run(conn, run_id)
    if report is None:
        raise HTTPException(404, "Ingestion run not found")
    return report


@app.get("/api/health")
def health(conn=Depends(connection)):
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
@app.get("/feed", include_in_schema=False)
@app.get("/feed/", include_in_schema=False)
@app.get("/listings", include_in_schema=False)
@app.get("/listings/", include_in_schema=False)
@app.get("/profile", include_in_schema=False)
@app.get("/profile/", include_in_schema=False)
@app.get("/recommendations", include_in_schema=False)
@app.get("/recommendations/", include_in_schema=False)
@app.get("/saved", include_in_schema=False)
@app.get("/saved/", include_in_schema=False)
@app.get("/applied", include_in_schema=False)
@app.get("/applied/", include_in_schema=False)
def profile_page():
    if not FRONTEND_ENTRY.is_file():
        raise HTTPException(503, 'The interface is unavailable. Build the frontend first.')
    return FileResponse(FRONTEND_ENTRY)


# Serve only UI assets; snapshots, .env, and data exports are not public routes.
app.mount("/web", StaticFiles(directory=db.ROOT / "web"), name="web")
app.mount("/profile/assets", StaticFiles(directory=db.ROOT / "frontend" / "dist" / "assets", check_dir=False), name="profile-assets")
