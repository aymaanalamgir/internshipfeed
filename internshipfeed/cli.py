"""Fetch and normalize listings, then persist them in PostgreSQL."""
import argparse
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .enrich import enrich_job
from .fetch import README_URL, SOURCE_URL, fetch_text
from .models import JobKeywords
from .normalize import normalize
from .source import parse_readme

logger = logging.getLogger(__name__)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ingest Canadian Tech Internships into PostgreSQL")
    parser.add_argument("--input", type=Path, help="Parse a local README snapshot instead of fetching GitHub")
    parser.add_argument("--output", type=Path, default=Path("data"), help="Snapshot and run report directory")
    parser.add_argument("--enrich", action="store_true", help="Fetch public job descriptions and keywords")
    parser.add_argument("--enrich-limit", type=int, default=10, help="Maximum job-page attempts (default: 10)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between enrichment attempts")
    args = parser.parse_args(argv)
    if args.enrich_limit < 0 or args.delay < 0:
        parser.error("Enrichment limit and delay must be nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args.output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    run = {"id": str(uuid.uuid4()), "started_at": started, "source": SOURCE_URL, "readme_url": README_URL,
           "input_path": str(args.input) if args.input else None, "status": "running",
           "scraped": 0, "inserted": 0, "updated": 0, "skipped_duplicates": 0, "failed": 0,
           "enrichment_attempts": 0, "enrichment_successes": 0, "enrichment_failed": 0, "errors": []}
    conn = None
    try:
        raw = args.input.read_bytes() if args.input else fetch_text(README_URL)[0].encode("utf-8")
        snapshot_id = hashlib.sha256(raw).hexdigest()
        snapshots = args.output / "snapshots"
        snapshots.mkdir(exist_ok=True)
        (snapshots / (snapshot_id + ".md")).write_bytes(raw)
        run["snapshot_id"] = snapshot_id
        records = parse_readme(raw.decode("utf-8"))
        run["scraped"] = len(records)
        write_json(args.output / "source_records.json", records)
        conn = db.connect()
        db.initialize(conn)
        seen, cache = set(), {}
        for record in records:
            try:
                job = normalize(record, SOURCE_URL, snapshot_id)
                identity = (job.source, job.external_id)
                if identity in seen:
                    run["skipped_duplicates"] += 1
                    continue
                if args.enrich and job.status == "open" and job.url:
                    job.enrichment_status = "not_attempted"
                    if run["enrichment_attempts"] < args.enrich_limit:
                        if run["enrichment_attempts"]:
                            time.sleep(args.delay)
                        logger.info("Enriching %s — %s", job.company, job.title)
                        enrichment = enrich_job(job, cache)
                        run["enrichment_attempts"] += 1
                        job.enrichment_status = enrichment["status"]
                        job.enrichment_error = enrichment.get("error")
                        if enrichment["status"] == "success":
                            job.description = enrichment["description"]
                            job.keywords = JobKeywords.model_validate(enrichment["keywords"])
                            job.extraction_method = enrichment["extraction_method"]
                            job.resolved_url = enrichment.get("resolved_url")
                            job.enriched_at = datetime.now(timezone.utc)
                            run["enrichment_successes"] += 1
                        elif enrichment["status"] in {"failed", "unavailable"}:
                            run["enrichment_failed"] += 1
                outcome = db.save_job(conn, job)
                run[outcome] += 1
                seen.add(identity)
            except Exception as exc:
                run["failed"] += 1
                error = {"source_line": record["source_line"], "error": "%s: %s" % (type(exc).__name__, exc)}
                run["errors"].append(error)
                logger.error("Job at source line %s failed: %s", record["source_line"], error["error"])
        run["status"] = "partial_failure" if run["failed"] else "success"
    except Exception as exc:
        run["status"] = "failed"
        run["error"] = "%s: %s" % (type(exc).__name__, exc)
        accounted = run["inserted"] + run["updated"] + run["skipped_duplicates"] + run["failed"]
        run["failed"] += run["scraped"] - accounted
        logger.error("Ingestion failed: %s", run["error"])
    finally:
        run["finished_at"] = datetime.now(timezone.utc).isoformat()
        if conn is not None:
            try:
                db.record_run(conn, run)
            except Exception as exc:
                run["report_persistence_error"] = "%s: %s" % (type(exc).__name__, exc)
                run["status"] = "failed"
                logger.error("Could not persist ingestion report: %s", type(exc).__name__)
            finally:
                conn.close()
        write_json(args.output / "run.json", run)
        runs = args.output / "runs"
        runs.mkdir(exist_ok=True)
        write_json(runs / (run["id"] + ".json"), run)
    print(json.dumps({key: run[key] for key in ("status", "scraped", "inserted", "updated", "skipped_duplicates", "failed", "enrichment_failed")}))
    return 0 if run["status"] == "success" else 1
