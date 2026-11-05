"""Database-backed API and the consolidated React product."""
import logging
import os
import secrets
from contextlib import asynccontextmanager
from uuid import UUID

from typing import List, Literal, Optional

import psycopg
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import applications, db, sessions
from .models import (ApplicationStatus, ApplicationUpdate, AppliedJobView, JobApplication, CandidateJobView, CandidateProfile, CandidateProfileUpdate, CanonicalJob,
                     InteractionReceipt, InteractionRequest, JobDetails, JobInteractionState, RecommendedJobView, Resume, ResumeMetadata, ResumeSkillSuggestions)
from .models import CanonicalJobPage, JobKeywords, JobListPage, ListingFilterOptions
from .matching import score_job
from .behavioral import build_behavior_profile
from .recommendations import rank_recommendations
from .resume_skills import extract_resume_skills, skill_aliases
from .resumes import MAX_UPLOAD_BYTES, ResumeExtractionError, extract_pdf_text
from .ingestion import SYNC_LOCK, recover_interrupted
from .sync import start_sync
from .scheduled_ingestion import run_scheduled_ingestion

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


@app.middleware('http')
async def private_api_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'private, no-store'
        vary = response.headers.get('Vary', '')
        response.headers['Vary'] = ', '.join(filter(None, [vary, 'Cookie']))
    return response


def current_candidate(request: Request, response: Response, conn=Depends(connection)):
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        sessions.user_mutation(request)
    user_id, candidate, created = sessions.resolve_candidate(conn, request.cookies.get(sessions.COOKIE_NAME))
    response.set_cookie(sessions.COOKIE_NAME, str(user_id), max_age=sessions.COOKIE_MAX_AGE,
                        expires=sessions.COOKIE_MAX_AGE, path='/', httponly=True,
                        secure=sessions.secure_cookie(request), samesite='lax')
    request.state.anonymous_created = created
    return candidate


@app.get('/api/session')
def session(request: Request, candidate=Depends(current_candidate)):
    return {'anonymous': True, 'created': request.state.anonymous_created}


@app.get("/api/sources")
def sources(conn=Depends(connection)):
    return db.source_directory(conn)


def catalog_page(status: Optional[Literal['open', 'closed', 'unknown']] = None,
                 q: Optional[str] = Query(default=None, max_length=200), source: Optional[str] = Query(default=None, max_length=2048),
                 source_id: Optional[int] = Query(default=None, gt=0, le=9223372036854775807),
                 location: Optional[str] = Query(default=None, max_length=1000),
                 term: Optional[str] = Query(default=None, max_length=200),
                 limit: int = Query(default=50, ge=1, le=100),
                 cursor: Optional[str] = Query(default=None, max_length=256),
                 conn=Depends(connection), candidate=Depends(current_candidate)):
    if source and source.isdecimal():
        if not 0 < int(source) <= 9223372036854775807 or (source_id is not None and int(source) != source_id):
            raise HTTPException(status_code=422, detail='Invalid source filter')
        source_id, source = int(source), None
    try:
        return db.list_job_summaries(conn, candidate['id'], limit=limit, cursor=cursor,
                                     status=status, query=q, source=source, source_id=source_id,
                                     location=location, term=term)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@app.get('/api/jobs', response_model=CanonicalJobPage)
def jobs(page=Depends(catalog_page)):
    return {**page.model_dump(), 'items': [item.job for item in page.items]}


@app.get('/api/feed', response_model=JobListPage)
def feed(page=Depends(catalog_page)):
    return page


@app.get('/api/feed/filters', response_model=ListingFilterOptions)
def listing_filters(conn=Depends(connection), candidate=Depends(current_candidate)):
    return db.listing_filter_options(conn, candidate['id'])


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


def local_ingestion_mutation(request):
    vercel = os.environ.get('VERCEL') == '1'
    development = vercel and os.environ.get('VERCEL_ENV') == 'development'
    if vercel and not development:
        raise HTTPException(403, "Hosted writes are not configured. Use the local development app.")
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "This action is only available on the local development app.")
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if development and request.headers.get('x-forwarded-host'):
        scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
        expected = scheme + '://' + request.headers['x-forwarded-host']
    if origin and origin != expected:
        raise HTTPException(403, "Cross-origin actions are not allowed.")


@app.get('/api/cron/ingest')
def cron_ingest(request: Request):
    secret = os.environ.get('CRON_SECRET', '')
    authorization = request.headers.get('authorization', '')
    if not secret or not secrets.compare_digest(authorization.encode(), ('Bearer ' + secret).encode()):
        raise HTTPException(401, 'Unauthorized')
    summary = run_scheduled_ingestion()
    return JSONResponse(summary, status_code=503 if summary['status'] in {'failed', 'partial_failure'} else 200,
                        headers={'Cache-Control': 'no-store'})


@app.get('/api/ingestion-status')
def ingestion_status(request: Request, conn=Depends(connection)):
    try:
        local_ingestion_mutation(request)
        allowed = True
    except HTTPException:
        allowed = False
    return {**db.sync_status(conn), 'manual_sync_allowed': allowed,
            'scheduled_sync_configured': bool(os.environ.get('CRON_SECRET'))}


@app.get("/api/profile", response_model=CandidateProfile)
def profile(candidate=Depends(current_candidate)):
    return candidate


def _job_collection(conn, candidate, view, min_score=0, application_status=None, limit=50, offset=0):
    profile = CandidateProfile.model_validate(candidate)
    tracked = applications.list_applications(conn, profile.id, application_status) if view == 'applied' else []
    application_by_job = {row['canonical_job_id']: row for row in tracked}
    entries = db.candidate_job_details(conn, profile.id, view,
                                      **({'job_ids': list(application_by_job)} if view == 'applied' else {}))
    details, states = [item for item, _ in entries], {item.job.id: state for item, state in entries}
    if view == 'recommended':
        behavior = build_behavior_profile(db.behavior_history(conn, profile.id))
        matches = rank_recommendations(profile, details, behavior, min_score=min_score)
    else:
        matches = [score_job(profile, item) for item in details]
    if view == 'applied':
        order = {row['canonical_job_id']: index for index, row in enumerate(tracked)}
        matches.sort(key=lambda match: order[match.job.id])
    elif view != 'recommended':
        field = {'saved': 'saved_at', 'applied': 'applied_at', 'dismissed': 'dismissed_at'}[view]
        matches.sort(key=lambda match: (getattr(states[match.job.id], field).timestamp(), match.job.id), reverse=True)
    result_model = RecommendedJobView if view == 'recommended' else AppliedJobView if view == 'applied' else CandidateJobView
    metadata = {item.job.id: item for item in details}
    return [result_model(**match.model_dump(), interaction_state=states[match.job.id],
                         term=metadata[match.job.id].term,
                         keywords=JobKeywords(**{field: getattr(metadata[match.job.id].keywords, field)[:6]
                                                for field in ('languages', 'frameworks', 'tools_and_platforms')}),
                         **({'application': application_by_job[match.job.id]} if view == 'applied' else {})) for match in matches[offset:offset + limit]]


@app.get("/api/recommendations", response_model=List[RecommendedJobView])
def recommendations(min_score: int = Query(default=0, ge=0, le=100), limit: int = Query(default=30, ge=1, le=100),
                    offset: int = Query(default=0, ge=0), conn=Depends(connection), candidate=Depends(current_candidate)):
    return _job_collection(conn, candidate, 'recommended', min_score, limit=limit, offset=offset)


@app.get("/api/saved", response_model=List[CandidateJobView])
def saved_jobs(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
               conn=Depends(connection), candidate=Depends(current_candidate)):
    return _job_collection(conn, candidate, 'saved', limit=limit, offset=offset)


@app.get("/api/applied", response_model=List[AppliedJobView])
def applied_jobs(status: Optional[ApplicationStatus] = None, limit: int = Query(default=50, ge=1, le=100),
                 offset: int = Query(default=0, ge=0), conn=Depends(connection), candidate=Depends(current_candidate)):
    return _job_collection(conn, candidate, 'applied', application_status=status, limit=limit, offset=offset)


@app.get("/api/dismissed", response_model=List[CandidateJobView])
def dismissed_jobs(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
                   conn=Depends(connection), candidate=Depends(current_candidate)):
    return _job_collection(conn, candidate, 'dismissed', limit=limit, offset=offset)


@app.put("/api/profile", response_model=CandidateProfile)
def update_profile(update: CandidateProfileUpdate, conn=Depends(connection), candidate=Depends(current_candidate)):
    return db.update_candidate_profile(conn, candidate['id'], update)


@app.get("/api/resumes/latest", response_model=Optional[ResumeMetadata])
def latest_resume(conn=Depends(connection), candidate=Depends(current_candidate)):
    return db.latest_resume_metadata(conn, candidate['id'])


@app.get("/api/resumes/{resume_id}/skill-suggestions", response_model=ResumeSkillSuggestions)
def resume_skill_suggestions(resume_id: int, conn=Depends(connection), candidate=Depends(current_candidate)):
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
def interactions(conn=Depends(connection), candidate=Depends(current_candidate)):
    return db.interaction_states(conn, candidate['id'])


@app.post("/api/jobs/{job_id}/interactions", response_model=InteractionReceipt, status_code=201)
def job_interaction(job_id: int, interaction: InteractionRequest, conn=Depends(connection), candidate=Depends(current_candidate)):
    try:
        return db.record_interaction_with_state(conn, candidate['id'], job_id, interaction.type)
    except ValueError:
        raise HTTPException(404, "Job not found") from None


@app.patch("/api/jobs/{job_id}/application", response_model=JobApplication)
def update_application(job_id: int, update: ApplicationUpdate, conn=Depends(connection), candidate=Depends(current_candidate)):
    result = applications.update_application(conn, candidate['id'], job_id, update.status)
    if result is None:
        raise HTTPException(404, "Application not found. Mark this job as applied first.")
    return result


@app.post("/api/sync", status_code=202)
def sync_jobs(request: Request):
    local_ingestion_mutation(request)
    try:
        return start_sync()
    except (psycopg.Error, RuntimeError):
        logger.exception("Could not start sync")
        raise HTTPException(503, "Unable to start sync. Please try again later.")


@app.post("/api/resumes", response_model=Resume, status_code=201)
def upload_resume(file: UploadFile = File(...), conn=Depends(connection), candidate=Depends(current_candidate)):
    try:
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
@app.get("/dismissed", include_in_schema=False)
@app.get("/dismissed/", include_in_schema=False)
def profile_page():
    if not FRONTEND_ENTRY.is_file():
        raise HTTPException(503, 'The interface is unavailable. Build the frontend first.')
    return FileResponse(FRONTEND_ENTRY)


# Serve only UI assets; snapshots, .env, and data exports are not public routes.
if os.environ.get('VERCEL') != '1':
    app.mount("/web", StaticFiles(directory=db.ROOT / "web"), name="web")
    app.mount("/profile/assets", StaticFiles(directory=db.ROOT / "frontend" / "dist" / "assets", check_dir=False), name="profile-assets")
