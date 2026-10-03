"""PostgreSQL persistence. The unique constraint resolves concurrent inserts."""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import JobListing

ROOT = Path(__file__).resolve().parent.parent


def connect(database_url=None):
    load_dotenv(ROOT / ".env", override=False)
    database_url = database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required. Configure .env or run python -m internshipfeed.dev_db.")
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=True, connect_timeout=5)


def initialize(conn):
    with conn.transaction():
        # Serialize the idempotent schema setup across app/scraper processes.
        conn.execute("SELECT pg_advisory_xact_lock(78112027)")
        conn.execute((Path(__file__).parent / "migrations" / "001_jobs.sql").read_text())


FIELDS = ("source", "external_id", "company", "title", "location", "url", "last_known_url",
          "posted_at", "term", "status", "description", "keywords", "enrichment_status",
          "enrichment_error", "extraction_method", "resolved_url", "enriched_at", "snapshot_id", "source_line")
CONTENT_FIELDS = ("company", "title", "location", "url", "last_known_url", "posted_at", "term", "status",
                  "description", "keywords")
ENRICHMENT_FIELDS = ("description", "keywords", "extraction_method", "resolved_url", "enriched_at")


def values(payload):
    return [Jsonb(payload[key]) if key == "keywords" else payload[key] for key in FIELDS]


def save_job(conn, job):
    """Return inserted/updated/skipped_duplicates; always refresh source lineage."""
    payload = job.model_dump()
    payload["last_known_url"] = job.url or job.last_known_url
    with conn.transaction():
        result = conn.execute(
            "INSERT INTO jobs (" + ", ".join(FIELDS) + ") VALUES (" + ", ".join(["%s"] * len(FIELDS)) + ") "
            "ON CONFLICT (source, external_id) DO NOTHING RETURNING id", values(payload)
        ).fetchone()
        if result:
            return "inserted"
        old = conn.execute("SELECT * FROM jobs WHERE source = %s AND external_id = %s FOR UPDATE",
                           (job.source, job.external_id)).fetchone()
        payload["last_known_url"] = job.url or old["last_known_url"] or old["url"]
        if job.enrichment_status != "success":
            # An unavailable page or an ordinary rerun must not erase good enrichment.
            for key in ENRICHMENT_FIELDS:
                payload[key] = old[key]
        if job.enrichment_status in {"not_requested", "not_attempted"}:
            payload["enrichment_status"] = old["enrichment_status"]
            payload["enrichment_error"] = old["enrichment_error"]
        changed = any(old[key] != payload[key] for key in CONTENT_FIELDS)
        conn.execute("UPDATE jobs SET " + ", ".join(key + " = %s" for key in FIELDS) +
                     ", last_seen_at = clock_timestamp() WHERE id = %s", values(payload) + [old["id"]])
        return "updated" if changed else "skipped_duplicates"


def list_jobs(conn, status=None, query=None):
    clauses, params = [], []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if query:
        clauses.append("concat_ws(' ', company, title, location, term, keywords::text) ILIKE %s")
        escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        params.append("%" + escaped + "%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    records = conn.execute("SELECT * FROM jobs" + where + " ORDER BY posted_at DESC NULLS LAST, id DESC", params).fetchall()
    return [JobListing.model_validate(record) for record in records]


def get_job(conn, job_id):
    record = conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
    return JobListing.model_validate(record) if record else None


def record_run(conn, run):
    conn.execute(
        "INSERT INTO ingestion_runs (id, started_at, finished_at, source, status, scraped, inserted, updated, skipped_duplicates, failed, report) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (run["id"], run["started_at"], run["finished_at"], run["source"], run["status"],
         run["scraped"], run["inserted"], run["updated"], run["skipped_duplicates"], run["failed"], Jsonb(run)),
    )


def latest_run(conn):
    row = conn.execute("SELECT report FROM ingestion_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    return row["report"] if row else None


def main():
    with connect() as conn:
        initialize(conn)
    print("PostgreSQL jobs and ingestion_runs tables are ready.")


if __name__ == "__main__":
    main()
