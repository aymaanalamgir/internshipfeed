"""PostgreSQL persistence. The unique constraint resolves concurrent inserts."""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import CanonicalJob, JobDetails, JobKeywords, JobListing, SourceJobReference
from .reconciliation import RECONCILIATION_LOCK, backfill, canonicalize_url, reconcile, root_id

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
        migrations = Path(__file__).parent / "migrations"
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
        for path in sorted(migrations.glob("*.sql")):
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (path.name,)).fetchone():
                # Older bootstraps did not record 001. Adopt it after a table
                # rename rather than recreating an empty legacy jobs table.
                adopted = path.name == "001_jobs.sql" and conn.execute("SELECT to_regclass('source_jobs') AS name").fetchone()["name"] is not None
                if not adopted:
                    conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
        # A long-running older app can discover a new SQL file before it has
        # loaded the Python migration hook. Repair that incomplete upgrade too.
        complete = conn.execute("SELECT attnotnull FROM pg_attribute WHERE attrelid = 'source_jobs'::regclass AND attname = 'canonical_job_id'").fetchone()
        if not complete["attnotnull"]:
            backfill(conn)


FIELDS = ("source", "external_id", "company", "title", "location", "url", "last_known_url",
          "posted_at", "term", "status", "description", "keywords", "enrichment_status",
          "enrichment_error", "extraction_method", "resolved_url", "enriched_at", "snapshot_id", "source_line", "link_checked_at")
CONTENT_FIELDS = ("company", "title", "location", "url", "last_known_url", "posted_at", "term", "status",
                  "description", "keywords")
ENRICHMENT_FIELDS = ("description", "keywords", "extraction_method", "resolved_url", "enriched_at")


def values(payload):
    return [Jsonb(payload[key]) if key == "keywords" and payload[key] is not None else payload[key] for key in FIELDS]


def save_job(conn, job):
    """Atomically upsert the source record and reconcile its canonical identity."""
    payload = job.model_dump()
    payload["last_known_url"] = job.url or job.last_known_url
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (RECONCILIATION_LOCK,))
        old = conn.execute("SELECT * FROM source_jobs WHERE source = %s AND external_id = %s FOR UPDATE",
                           (job.source, job.external_id)).fetchone()
        if old:
            payload["last_known_url"] = job.url or old["last_known_url"] or old["url"]
            same_url = not job.url or canonicalize_url(job.url) == canonicalize_url(old["url"] or old["last_known_url"])
            if job.link_checked_at is None and same_url:
                payload["link_checked_at"] = old["link_checked_at"]
            if job.enrichment_status != "success":
                for key in ENRICHMENT_FIELDS:
                    payload[key] = old[key] if same_url else (JobKeywords().model_dump() if key == "keywords" else None)
            if job.enrichment_status in {"not_requested", "not_attempted"} and same_url:
                payload["enrichment_status"] = old["enrichment_status"]
                payload["enrichment_error"] = old["enrichment_error"]
        canonical_id = reconcile(conn, payload, previous=old)
        if old is None:
            conn.execute("INSERT INTO source_jobs (" + ", ".join(FIELDS) + ", canonical_job_id) VALUES (" +
                         ", ".join(["%s"] * (len(FIELDS) + 1)) + ")", values(payload) + [canonical_id])
            return "inserted"
        changed = any(old[key] != payload[key] for key in CONTENT_FIELDS)
        conn.execute("UPDATE source_jobs SET " + ", ".join(key + " = %s" for key in FIELDS) +
                     ", canonical_job_id = %s, last_seen_at = clock_timestamp() WHERE id = %s",
                     values(payload) + [canonical_id, old["id"]])
        return "updated" if changed else "skipped_duplicates"


def list_source_jobs(conn, status=None, query=None, include_inactive=False, source=None):
    clauses, params = ([] if include_inactive else ["status NOT IN ('closed', 'dead')"]), []
    if source:
        clauses.append("source = %s")
        params.append(source)
    if status:
        clauses.append("status = %s")
        params.append(status)
    if query:
        clauses.append("concat_ws(' ', company, title, location, term, keywords::text) ILIKE %s")
        escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        params.append("%" + escaped + "%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    records = conn.execute("SELECT * FROM source_jobs" + where + " ORDER BY posted_at DESC NULLS LAST, id DESC", params).fetchall()
    return [JobListing.model_validate(record) for record in records]


def get_source_job(conn, source, external_id):
    record = conn.execute("SELECT * FROM source_jobs WHERE source = %s AND external_id = %s", (source, external_id)).fetchone()
    return JobListing.model_validate(record) if record else None


def get_run(conn, run_id):
    row = conn.execute("SELECT report FROM ingestion_runs WHERE id = %s", (run_id,)).fetchone()
    return row["report"] if row else None


def _job_details(job, members):
    priority = {"open": 0, "unknown": 1, "closed": 2, "dead": 3}
    ordered = sorted(members, key=lambda row: (priority[row["status"]], row["posted_at"] is None, row["id"]))
    descriptions = [row for row in ordered if row["description"]]
    descriptions.sort(key=lambda row: (row["status"] in {"closed", "dead"},
                                       -(row["enriched_at"].timestamp() if row["enriched_at"] else 0), row["id"]))
    keyword_data = {key: list(dict.fromkeys(value for row in ordered for value in row["keywords"].get(key, [])))
                    for key in JobKeywords.model_fields}
    references = [SourceJobReference.model_validate({key: row[key] for key in SourceJobReference.model_fields})
                  for row in sorted(members, key=lambda row: row["id"])]
    return JobDetails(job=job, term=ordered[0]["term"],
                      description=descriptions[0]["description"] if descriptions else None,
                      keywords=JobKeywords(**keyword_data), sources=references)


def list_job_details(conn, status=None, query=None, include_inactive=False, source=None):
    # Read core canonical data from the seven-column database projection. Context
    # is fetched in a separate batch, without widening the CanonicalJob model.
    with conn.transaction():
        canonical = conn.execute("SELECT * FROM canonical_jobs").fetchall()
        observations = conn.execute("SELECT * FROM source_jobs ORDER BY id").fetchall()
    groups = {}
    for row in observations:
        groups.setdefault(row["canonical_job_id"], []).append(row)
    result = []
    for row in canonical:
        job = CanonicalJob.model_validate(row)
        if not include_inactive and job.status in {"closed", "dead"} or status and job.status != status:
            continue
        members = groups.get(job.id, [])
        if not members:
            continue
        eligible = members if include_inactive else [item for item in members if item["status"] not in {"closed", "dead"}]
        if source and not any(item["source"] == source for item in eligible):
            continue
        if query:
            text = " ".join(str(item.get(field) or "") for item in members
                            for field in ("company", "title", "location", "term", "keywords"))
            if query.casefold() not in text.casefold():
                continue
        result.append(_job_details(job, members))
    result.sort(key=lambda details: (details.job.posted_at is not None,
                                    details.job.posted_at.toordinal() if details.job.posted_at else 0, details.job.id), reverse=True)
    return result


def list_jobs(conn, status=None, query=None, include_inactive=False, source=None):
    return [details.job for details in list_job_details(conn, status=status, query=query,
                                                       include_inactive=include_inactive, source=source)]


def get_job(conn, job_id):
    canonical_id = root_id(conn, job_id)
    if canonical_id is None:
        return None
    row = conn.execute("SELECT * FROM canonical_jobs WHERE id = %s AND status NOT IN ('closed', 'dead')", (canonical_id,)).fetchone()
    return CanonicalJob.model_validate(row) if row else None


def get_job_details(conn, job_id):
    job = get_job(conn, job_id)
    if job is None:
        return None
    members = conn.execute("SELECT * FROM source_jobs WHERE canonical_job_id = %s ORDER BY id", (job.id,)).fetchall()
    return _job_details(job, members) if members else None


def reconciliation_stats(conn):
    return conn.execute("""
        WITH groups AS (
            SELECT canonical_job_id, count(*) AS members, count(DISTINCT source) AS source_count,
                   count(*) FILTER (WHERE status NOT IN ('closed', 'dead')) AS active_members
            FROM source_jobs GROUP BY canonical_job_id
        )
        SELECT COALESCE(sum(members), 0)::bigint AS source_records,
               count(*) AS canonical_jobs,
               count(*) FILTER (WHERE source_count > 1) AS cross_source_groups,
               COALESCE(sum(active_members), 0)::bigint AS active_source_records,
               count(*) FILTER (WHERE active_members > 0) AS visible_jobs,
               (COALESCE(sum(active_members), 0) - count(*) FILTER (WHERE active_members > 0))::bigint AS duplicate_listings_removed
        FROM groups
    """).fetchone()


def record_run(conn, run):
    conn.execute(
        "INSERT INTO ingestion_runs (id, started_at, finished_at, source, status, scraped, inserted, updated, skipped_duplicates, failed, report) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET finished_at = EXCLUDED.finished_at, status = EXCLUDED.status, "
        "scraped = EXCLUDED.scraped, inserted = EXCLUDED.inserted, updated = EXCLUDED.updated, "
        "skipped_duplicates = EXCLUDED.skipped_duplicates, failed = EXCLUDED.failed, report = EXCLUDED.report",
        (run["id"], run["started_at"], run["finished_at"], run["source"], run["status"],
         run["scraped"], run["inserted"], run["updated"], run["skipped_duplicates"], run["failed"], Jsonb(run)),
    )


def latest_run(conn):
    row = conn.execute("SELECT report FROM ingestion_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    return row["report"] if row else None


def main():
    with connect() as conn:
        initialize(conn)
    print("PostgreSQL source_jobs, canonical_jobs, and ingestion_runs tables are ready.")


if __name__ == "__main__":
    main()
