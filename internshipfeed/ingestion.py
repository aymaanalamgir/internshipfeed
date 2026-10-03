"""One workflow: fetch listings, read descriptions, transform, and persist."""
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .ai import DescriptionTransformer, PROMPT_VERSION, description_hash
from .enrich import enrich_job
from .fetch import README_URL, SOURCE_URL, fetch_text
from .models import JobKeywords
from .normalize import normalize
from .source import parse_readme

logger = logging.getLogger(__name__)
SYNC_LOCK = 78112028


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def new_run():
    return {"id": str(uuid.uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "source": SOURCE_URL, "readme_url": README_URL,
            "status": "running", "stage": "fetching", "scraped": 0, "processed": 0,
            "inserted": 0, "updated": 0, "skipped_duplicates": 0, "failed": 0,
            "enrichment_attempts": 0, "enrichment_successes": 0, "enrichment_failed": 0,
            "enrichment_cached": 0, "enrichment_skipped": 0, "closed": 0, "dead_links": 0,
            "ai_attempts": 0, "ai_successes": 0, "ai_failed": 0, "ai_cached": 0,
            "ai_not_configured": 0, "ai_deferred": 0, "errors": []}


def recover_interrupted(conn):
    # Caller owns the session lock, so any persisted running row is orphaned.
    rows = conn.execute("SELECT report FROM ingestion_runs WHERE status = 'running' AND source = %s", (SOURCE_URL,)).fetchall()
    for row in rows:
        report = row["report"]
        report.update(status="failed", stage="finished", finished_at=datetime.now(timezone.utc).isoformat(),
                      error="The previous sync was interrupted. Start a new sync to resume.")
        accounted = sum(report.get(key, 0) for key in ("inserted", "updated", "skipped_duplicates", "failed"))
        report["failed"] = report.get("failed", 0) + max(0, report.get("scraped", 0) - accounted)
        db.record_run(conn, report)


def retain_description(job, old):
    if old and old.description:
        for key in ("description", "keywords", "extraction_method", "resolved_url", "enriched_at"):
            setattr(job, key, getattr(old, key))


def process_description(job, old, run, cache, transformer, enrich, limit, force, delay, use_ai):
    if job.status == "closed":
        run["closed"] += 1
        run["enrichment_skipped"] += 1
        job.ai_status = "skipped"
        return
    if old and old.status == "dead" and old.url == job.url and old.link_checked_at and old.link_checked_at > datetime.now(timezone.utc) - timedelta(days=1) and not force:
        job.status = "dead"
        job.ai_status = "skipped"
        run["dead_links"] += 1
        run["enrichment_skipped"] += 1
        return
    fresh = old and old.status == "open" and old.description and old.enriched_at and old.url == job.url and old.enriched_at > datetime.now(timezone.utc) - timedelta(days=1)
    if enrich and job.status == "open" and job.url:
        if fresh and not force:
            retain_description(job, old)
            run["enrichment_cached"] += 1
        elif limit is None or run["enrichment_attempts"] < limit:
            if run["enrichment_attempts"]:
                time.sleep(delay)
            enrichment = enrich_job(job, cache)
            run["enrichment_attempts"] += 1
            job.link_checked_at = datetime.now(timezone.utc)
            job.enrichment_status = enrichment["status"]
            job.enrichment_error = enrichment.get("error")
            if enrichment.get("dead"):
                job.status = "dead"
                job.ai_status = "skipped"
                run["dead_links"] += 1
                run["enrichment_skipped"] += 1
                return
            if enrichment["status"] == "success":
                job.description = enrichment["description"]
                job.keywords = JobKeywords.model_validate(enrichment["keywords"])
                job.extraction_method = enrichment["extraction_method"]
                job.resolved_url = enrichment.get("resolved_url")
                job.enriched_at = datetime.now(timezone.utc)
                run["enrichment_successes"] += 1
            else:
                retain_description(job, old)
                key = "enrichment_failed" if enrichment["status"] in {"failed", "unavailable"} else "enrichment_skipped"
                run[key] += 1
        else:
            job.enrichment_status = "not_attempted"
            retain_description(job, old)
    else:
        retain_description(job, old)
        run["enrichment_skipped"] += 1
    # An explicitly metadata-only run must not resurrect a confirmed dead link.
    if old and old.status == "dead" and old.url == job.url and job.enrichment_status != "success":
        job.status = "dead"
        job.ai_status = "skipped"
        return
    if not use_ai:
        return
    if not job.description:
        job.ai_status = "skipped"
        job.ai_error = "No readable description is available for analysis."
        return
    fingerprint = description_hash(job.description)
    if old and old.ai_analysis and old.ai_description_hash == fingerprint and old.ai_model == transformer.model and old.ai_prompt_version == PROMPT_VERSION:
        for key in ("ai_analysis", "ai_model", "ai_description_hash", "ai_prompt_version", "ai_enriched_at"):
            setattr(job, key, getattr(old, key))
        job.ai_status = "success"
        run["ai_cached"] += 1
        return
    if transformer.configured and not transformer.disabled_error:
        run["ai_attempts"] += 1
    result = transformer.transform(job.description)
    job.ai_status = result["status"]
    job.ai_error = result.get("error")
    if result["status"] == "success":
        job.ai_analysis = result["analysis"]
        job.ai_model = result["model"]
        job.ai_description_hash = result["description_hash"]
        job.ai_prompt_version = result["prompt_version"]
        job.ai_enriched_at = result["analyzed_at"]
        run["ai_successes"] += 1
    elif result["status"] == "not_configured":
        run["ai_not_configured"] += 1
    elif result.get("deferred"):
        run["ai_deferred"] += 1
    else:
        run["ai_failed"] += 1


def run_ingestion(input_path=None, output=None, enrich=True, enrichment_limit=None,
                  force=False, delay=1.0, use_ai=True, conn=None, run=None, owns_lock=False):
    output = Path(output or db.ROOT / "data")
    output.mkdir(parents=True, exist_ok=True)
    run = run or new_run()
    run["input_path"] = str(input_path) if input_path else None
    transformer = DescriptionTransformer()
    lock_acquired = owns_lock
    try:
        conn = conn or db.connect()
        db.initialize(conn)
        if not lock_acquired:
            lock_acquired = conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (SYNC_LOCK,)).fetchone()["acquired"]
            if not lock_acquired:
                raise RuntimeError("A sync is already running. View its progress in the UI.")
            recover_interrupted(conn)
        db.record_run(conn, run)
        raw = Path(input_path).read_bytes() if input_path else fetch_text(README_URL)[0].encode("utf-8")
        snapshot_id = hashlib.sha256(raw).hexdigest()
        snapshots = output / "snapshots"
        snapshots.mkdir(exist_ok=True)
        (snapshots / (snapshot_id + ".md")).write_bytes(raw)
        run["snapshot_id"] = snapshot_id
        records = parse_readme(raw.decode("utf-8"))
        run["scraped"] = len(records)
        write_json(output / "source_records.json", records)
        db.record_run(conn, run)
        seen, cache = set(), {}
        for record in records:
            try:
                job = normalize(record, SOURCE_URL, snapshot_id)
                identity = (job.source, job.external_id)
                run.update(stage="enriching", current_job="%s — %s" % (job.company, job.title))
                db.record_run(conn, run)
                if identity in seen:
                    run["skipped_duplicates"] += 1
                else:
                    old = db.get_source_job(conn, job.source, job.external_id)
                    process_description(job, old, run, cache, transformer, enrich, enrichment_limit, force, delay, use_ai)
                    run[db.save_job(conn, job)] += 1
                    seen.add(identity)
            except Exception as exc:
                run["failed"] += 1
                run["errors"].append({"source_line": record["source_line"], "error": "%s: %s" % (type(exc).__name__, exc)})
                logger.error("Job at line %s failed (%s)", record["source_line"], type(exc).__name__)
            run["processed"] += 1
            db.record_run(conn, run)
        run["status"] = "partial_failure" if run["failed"] else "success"
    except Exception as exc:
        run.update(status="failed", error="%s: %s" % (type(exc).__name__, exc))
        accounted = sum(run[key] for key in ("inserted", "updated", "skipped_duplicates", "failed"))
        run["failed"] += max(0, run["scraped"] - accounted)
        logger.error("Sync failed (%s)", type(exc).__name__)
    finally:
        run.update(finished_at=datetime.now(timezone.utc).isoformat(), stage="finished")
        run.pop("current_job", None)
        transformer.close()
        if conn is not None:
            try:
                if lock_acquired:
                    db.record_run(conn, run)
            except Exception as exc:
                run.update(status="failed", report_persistence_error=type(exc).__name__)
            finally:
                conn.close()
        write_json(output / "run.json", run)
        runs = output / "runs"
        runs.mkdir(exist_ok=True)
        write_json(runs / (run["id"] + ".json"), run)
    return run
