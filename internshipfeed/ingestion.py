"""One workflow: fetch listings, read descriptions, transform, and persist."""
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .enrich import enrich_job
from .fetch import fetch_text
from .sources import CANADIAN
from .models import JobKeywords
from .normalize import normalize

logger = logging.getLogger(__name__)
SYNC_LOCK = 78112028


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


COUNTERS = ("scraped", "parsed", "processed", "inserted", "updated", "skipped_duplicates", "failed",
            "enrichment_attempts", "enrichment_successes", "enrichment_failed",
            "enrichment_cached", "enrichment_skipped", "closed", "dead_links")


def new_run():
    return {"id": str(uuid.uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "source": "all", "status": "running", "stage": "fetching",
            "sources": [], "errors": [], **dict.fromkeys(COUNTERS, 0)}


def recover_interrupted(conn):
    # Caller owns the session lock, so any persisted running row is orphaned.
    rows = conn.execute("SELECT report FROM ingestion_batches WHERE status = 'running'", ()).fetchall()
    for row in rows:
        report = row["report"]
        report.update(status="failed", stage="finished", finished_at=datetime.now(timezone.utc).isoformat(),
                      error="The previous sync was interrupted. Start a new sync to resume.")
        accounted = sum(report.get(key, 0) for key in ("inserted", "updated", "skipped_duplicates", "failed"))
        report["failed"] = report.get("failed", 0) + max(0, report.get("scraped", 0) - accounted)
        db.record_batch(conn, report)
        conn.execute("""UPDATE ingestion_runs SET status='failed', finished_at=clock_timestamp(),
            error_summary='Sync interrupted; resume from retained snapshots and postings.',
            records_failed=GREATEST(records_failed, records_fetched-records_inserted-records_updated-records_skipped)
            WHERE batch_id=%s AND status='running'""", (report['id'],))


def retain_description(job, old):
    if old and old.description:
        for key in ("description", "keywords", "extraction_method", "resolved_url", "enriched_at"):
            setattr(job, key, getattr(old, key))


def process_description(job, old, run, cache, enrich, limit, force, delay):
    if job.status == "closed":
        run["closed"] += 1
        run["enrichment_skipped"] += 1
        return
    if old and old.status == "dead" and old.url == job.url and old.link_checked_at and old.link_checked_at > datetime.now(timezone.utc) - timedelta(days=1) and not force:
        job.status = "dead"
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
        return

def run_ingestion(input_path=None, output=None, enrich=True, enrichment_limit=None,
                  force=False, delay=1.0, conn=None, run=None, owns_lock=False,
                  source_keys=None, input_paths=None):
    """Run each selected source independently, using the same normalizer and repository.

    input_path replays one source (Canadian by default). input_paths maps source keys
    to snapshots and is useful for reproducible multi-source ingestion.
    """
    output = Path(output or db.ROOT / "data")
    run = run or new_run()
    lock_acquired = owns_lock
    try:
        output.mkdir(parents=True, exist_ok=True)
        conn = conn or db.connect()
        db.initialize(conn)
        keys = source_keys
        inputs = dict(input_paths or {})
        if input_path:
            if source_keys and len(source_keys) != 1:
                raise ValueError("--input requires exactly one --source")
            keys = source_keys or [CANADIAN.key]
            inputs[keys[0]] = input_path
        selected = db.configured_sources(conn, keys)
        if not lock_acquired:
            lock_acquired = conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (SYNC_LOCK,)).fetchone()["acquired"]
            if not lock_acquired:
                raise RuntimeError("A sync is already running. View its progress in the UI.")
            recover_interrupted(conn)
        db.record_batch(conn, run)
        snapshots = output / "snapshots"
        snapshots.mkdir(exist_ok=True)
        seen, cache, raw_records = set(), {}, []
        source_failures = 0
        for configured, source in selected:
            report = {"key": source.key, "name": source.name, "source": source.url,
                      "readme_url": source.readme_url, "source_id": configured["id"], "status": "running", **dict.fromkeys(COUNTERS, 0)}
            report["id"] = str(db.start_source_run(conn, configured["id"], run["id"]))
            run["sources"].append(report)
            run.update(stage="fetching", current_source=source.name)
            db.record_batch(conn, run)
            try:
                if source.key in inputs:
                    path = Path(inputs[source.key])
                    raw = path.read_bytes()
                    report["input_path"] = str(path)
                else:
                    markdown, resolved_url = fetch_text(source.readme_url)
                    raw = markdown.encode("utf-8")
                    report["resolved_url"] = resolved_url
                snapshot_id = hashlib.sha256(raw).hexdigest()
                snapshot = db.save_snapshot(conn, configured["id"], raw.decode("utf-8"))
                report["snapshot_id"] = snapshot["id"]
                report["content_hash"] = snapshot_id
                db.record_source_run(conn, report["id"], report, snapshot["id"])
                (snapshots / (snapshot_id + ".md")).write_bytes(raw)
                records = source.parse(raw.decode("utf-8"))
                report["scraped"] = len(records)
                run["scraped"] += len(records)
                raw_records.extend({"source": source.url, "source_key": source.key, "snapshot_id": snapshot["id"],
                                    **record.model_dump()} for record in records)
                write_json(output / "source_records.json", raw_records)
                db.record_batch(conn, run)
                for record in records:
                    before = {key: run[key] for key in COUNTERS}
                    try:
                        job = normalize(record, source.url, str(snapshot["id"]))
                        job.source_id = configured["id"]
                        run["parsed"] += 1
                        identity = (configured["id"], job.external_id)
                        run.update(stage="enriching", current_job="%s — %s" % (job.company, job.title))
                        db.record_batch(conn, run)
                        if identity in seen:
                            run["skipped_duplicates"] += 1
                        else:
                            old = db.get_source_job(conn, configured["id"], job.external_id)
                            process_description(job, old, run, cache, enrich, enrichment_limit, force, delay)
                            run[db.save_job(conn, job)] += 1
                            seen.add(identity)
                    except Exception as exc:
                        run["failed"] += 1
                        run["errors"].append({"source": source.url, "source_line": record.source_line,
                                              "error": "%s: %s" % (type(exc).__name__, exc)})
                        logger.error("%s line %s failed (%s)", source.key, record.source_line, type(exc).__name__)
                    run["processed"] += 1
                    for key in COUNTERS:
                        if key != "scraped":
                            report[key] += run[key] - before[key]
                    db.record_batch(conn, run)
                    db.record_source_run(conn, report["id"], report)
                report["status"] = "partial_failure" if report["failed"] else "success"
                if report["failed"]:
                    report["error"] = "%s source records failed validation or storage" % report["failed"]
            except Exception as exc:
                source_failures += 1
                report.update(status="failed", error="%s: %s" % (type(exc).__name__, exc))
                run["errors"].append({"source": source.url, "error": report["error"]})
                accounted = sum(report[key] for key in ("inserted", "updated", "skipped_duplicates", "failed"))
                remaining = max(0, report["scraped"] - accounted)
                report["failed"] += remaining
                run["failed"] += remaining
                logger.error("Source %s failed (%s); continuing other sources", source.key, type(exc).__name__)
            db.record_source_run(conn, report["id"], report)
            db.record_batch(conn, run)
        run["reconciliation"] = db.reconciliation_stats(conn)
        run["source_failures"] = source_failures
        run["status"] = "failed" if selected and source_failures == len(selected) else "partial_failure" if source_failures or run["failed"] else "success"
    except Exception as exc:
        run.update(status="failed", error="%s: %s" % (type(exc).__name__, exc))
        accounted = sum(run[key] for key in ("inserted", "updated", "skipped_duplicates", "failed"))
        run["failed"] += max(0, run["scraped"] - accounted)
        logger.error("Sync failed (%s)", type(exc).__name__)
    finally:
        run.update(finished_at=datetime.now(timezone.utc).isoformat(), stage="finished")
        run.pop("current_job", None)
        run.pop("current_source", None)
        if conn is not None:
            try:
                if lock_acquired:
                    db.record_batch(conn, run)
            except Exception as exc:
                run.update(status="failed", report_persistence_error=type(exc).__name__)
            finally:
                conn.close()
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "run.json", run)
        runs = output / "runs"
        runs.mkdir(exist_ok=True)
        write_json(runs / (run["id"] + ".json"), run)
    return run
