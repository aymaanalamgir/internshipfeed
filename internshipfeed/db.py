"""Relational PostgreSQL repository for source lineage and canonical opportunities."""
import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import CandidateProfileUpdate, CanonicalJob, JobDetails, JobInteractionState, JobKeywords, JobListing, SourceJobReference
from .reconciliation import RECONCILIATION_LOCK, backfill, canonicalize_url, link_posting, reconcile, refresh_canonical, root_id
from .sources import SOURCES, get_source

ROOT = Path(__file__).resolve().parent.parent
CORE_FIELDS = tuple(CanonicalJob.model_fields)


def connect(database_url=None):
    load_dotenv(ROOT / '.env', override=False)
    database_url = database_url or os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL is required. Configure .env or run python -m internshipfeed.dev_db.')
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=True, connect_timeout=5)


def initialize(conn):
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(78112027)')
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)')
        for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            if not conn.execute('SELECT 1 FROM schema_migrations WHERE version = %s', (path.name,)).fetchone():
                adopted = path.name == '001_jobs.sql' and conn.execute("SELECT COALESCE(to_regclass('source_postings'), to_regclass('source_jobs')) AS name").fetchone()['name'] is not None
                if not adopted:
                    conn.execute(path.read_text())
                conn.execute('INSERT INTO schema_migrations (version) VALUES (%s)', (path.name,))
        marker = '007_relational_backfill'
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version = %s', (marker,)).fetchone():
            _recover_legacy_content(conn)
            backfill(conn)
            conn.execute('INSERT INTO schema_migrations (version) VALUES (%s)', (marker,))
        elif conn.execute('SELECT 1 FROM source_postings p LEFT JOIN canonical_job_sources m ON m.source_posting_id=p.id WHERE m.source_posting_id IS NULL LIMIT 1').fetchone():
            backfill(conn)


def ensure_source(conn, repository):
    rows = conn.execute('SELECT * FROM job_sources WHERE repository = %s ORDER BY id', (repository,)).fetchall()
    if len(rows) > 1:
        raise ValueError('Repository has multiple datasets; provide source_id')
    if rows:
        return rows[0]
    key = 'legacy:' + hashlib.sha256(repository.encode()).hexdigest()
    return conn.execute("""INSERT INTO job_sources (key, name, provider_type, repository, file_path, branch, enabled)
        VALUES (%s, %s, 'legacy', %s, 'README.md', 'unknown', false)
        ON CONFLICT (repository, file_path, branch) DO UPDATE SET repository=EXCLUDED.repository RETURNING *""",
        (key, repository, repository)).fetchone()


def configured_sources(conn, keys=None):
    rows = conn.execute('SELECT * FROM job_sources WHERE enabled ORDER BY id').fetchall()
    if keys:
        unknown = set(keys) - {row['key'] for row in conn.execute('SELECT key FROM job_sources').fetchall()}
        if unknown:
            raise ValueError('Unknown source keys: ' + ', '.join(sorted(unknown)))
        rows = [row for row in rows if row['key'] in keys]
    result = []
    for row in rows:
        if row['provider_type'] != 'github':
            raise ValueError('No fetcher for enabled source provider ' + row['provider_type'])
        template = get_source(row['key'])
        path = urlparse(row['repository']).path.strip('/')
        raw_url = 'https://raw.githubusercontent.com/' + path + '/' + quote(row['branch'], safe='') + '/' + quote(row['file_path'], safe='/')
        result.append((row, replace(template, name=row['name'], url=row['repository'], readme_url=raw_url)))
    return result


def source_directory(conn):
    return conn.execute('SELECT id, key, name, provider_type, repository AS url, file_path, branch, enabled, created_at FROM job_sources ORDER BY id').fetchall()


def save_snapshot(conn, source_id, raw_content, commit_sha=None):
    content_hash = hashlib.sha256(raw_content.encode('utf-8')).hexdigest()
    return conn.execute("""INSERT INTO source_snapshots (source_id, content_hash, commit_sha, raw_content)
        VALUES (%s, %s, %s, %s) ON CONFLICT (source_id, content_hash) DO UPDATE SET
        raw_content = COALESCE(source_snapshots.raw_content, EXCLUDED.raw_content),
        commit_sha = COALESCE(source_snapshots.commit_sha, EXCLUDED.commit_sha) RETURNING *""",
        (source_id, content_hash, commit_sha, raw_content)).fetchone()


def _recover_legacy_content(conn):
    # Register hashes from historical reports, even if no current posting refers to them.
    for run in conn.execute('SELECT id, source_id, report FROM ingestion_runs WHERE snapshot_id IS NULL').fetchall():
        value = run['report'].get('snapshot_id')
        if isinstance(value, str) and re.fullmatch('[0-9a-fA-F]{64}', value):
            snapshot = conn.execute('INSERT INTO source_snapshots (source_id, content_hash) VALUES (%s, %s) ON CONFLICT (source_id, content_hash) DO UPDATE SET content_hash=EXCLUDED.content_hash RETURNING id', (run['source_id'], value)).fetchone()
            conn.execute('UPDATE ingestion_runs SET snapshot_id=%s WHERE id=%s', (snapshot['id'], run['id']))
    for snapshot in conn.execute('SELECT * FROM source_snapshots WHERE raw_content IS NULL').fetchall():
        digest = snapshot['content_hash']
        if not re.fullmatch('[0-9a-fA-F]{64}', digest):
            continue
        path = ROOT / 'data' / 'snapshots' / (digest + '.md')
        if path.is_file():
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() == digest.lower():
                conn.execute('UPDATE source_snapshots SET raw_content=%s WHERE id=%s', (raw.decode('utf-8'), snapshot['id']))
    # Raw legacy cells are recovered only from verified retained upstream text.
    from .normalize import normalize
    snapshots = conn.execute('SELECT snap.*, s.key, s.repository FROM source_snapshots snap JOIN job_sources s ON s.id=snap.source_id WHERE snap.raw_content IS NOT NULL').fetchall()
    for snapshot in snapshots:
        try:
            records = get_source(snapshot['key']).parse(snapshot['raw_content'])
        except ValueError:
            continue
        for record in records:
            job = normalize(record, snapshot['repository'], str(snapshot['id']))
            conn.execute('UPDATE source_postings SET raw_company=%s, raw_title=%s, raw_location=%s, raw_url=%s, raw_posted_at=%s, raw_row=%s WHERE source_id=%s AND snapshot_id=%s AND external_id=%s AND raw_company IS NULL',
                         (job.raw_company, job.raw_title, job.raw_location, job.raw_url, job.raw_posted_at, job.raw_row,
                          snapshot['source_id'], snapshot['id'], job.external_id))


POSTING_FIELDS = ('source_id', 'snapshot_id', 'external_id', 'raw_company', 'raw_title', 'raw_location', 'raw_url', 'raw_posted_at', 'raw_row',
                  'company', 'title', 'location', 'application_url', 'posted_at', 'status', 'last_known_url', 'term',
                  'description', 'keywords', 'enrichment_status', 'enrichment_error', 'extraction_method', 'resolved_url', 'enriched_at', 'source_line', 'link_checked_at')
CONTENT_FIELDS = ('company', 'title', 'location', 'application_url', 'posted_at', 'status', 'last_known_url', 'term', 'description', 'keywords',
                  'raw_company', 'raw_title', 'raw_location', 'raw_url', 'raw_posted_at', 'raw_row')
ENRICHMENT_FIELDS = ('description', 'keywords', 'extraction_method', 'resolved_url', 'enriched_at')
SOURCE_SELECT = """SELECT p.*, p.application_url AS url, s.repository AS source, m.canonical_job_id,
                   m.match_method, m.match_confidence, m.matched_url
                   FROM source_postings p JOIN job_sources s ON s.id=p.source_id
                   LEFT JOIN canonical_job_sources m ON m.source_posting_id=p.id"""


def _source_job(row):
    data = {key: row[key] for key in JobListing.model_fields if key in row}
    data['snapshot_id'] = str(data['snapshot_id'])
    return JobListing.model_validate(data)


def save_job(conn, job):
    """Commit a source observation, explainable mapping and canonical metadata together."""
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (RECONCILIATION_LOCK,))
        configured = conn.execute('SELECT * FROM job_sources WHERE id=%s', (job.source_id,)).fetchone() if job.source_id else ensure_source(conn, job.source)
        if configured is None or configured['repository'] != job.source:
            raise ValueError('Posting source_id does not match its source repository')
        try:
            snapshot_id = int(job.snapshot_id)
        except (ValueError, TypeError):
            raise ValueError('A stored source snapshot ID is required')
        if not conn.execute('SELECT 1 FROM source_snapshots WHERE id=%s AND source_id=%s', (snapshot_id, configured['id'])).fetchone():
            raise ValueError('Posting snapshot must belong to its source')
        payload = job.model_dump()
        payload.update(source_id=configured['id'], snapshot_id=snapshot_id, application_url=job.url,
                       last_known_url=job.url or job.last_known_url)
        old = conn.execute(SOURCE_SELECT + ' WHERE p.source_id=%s AND p.external_id=%s FOR UPDATE OF p', (configured['id'], job.external_id)).fetchone()
        if old:
            payload['last_known_url'] = job.url or old['last_known_url'] or old['url']
            same_url = not job.url or canonicalize_url(job.url) == canonicalize_url(old['url'] or old['last_known_url'])
            if job.link_checked_at is None and same_url:
                payload['link_checked_at'] = old['link_checked_at']
            if job.enrichment_status != 'success':
                for key in ENRICHMENT_FIELDS:
                    payload[key] = old[key] if same_url else (JobKeywords().model_dump() if key == 'keywords' else None)
            if job.enrichment_status in {'not_requested', 'not_attempted'} and same_url:
                payload['enrichment_status'], payload['enrichment_error'] = old['enrichment_status'], old['enrichment_error']
        decision = reconcile(conn, payload, previous=old)
        parameters = [Jsonb(payload[key]) if key == 'keywords' else payload[key] for key in POSTING_FIELDS]
        if old is None:
            posting_id = conn.execute('INSERT INTO source_postings (' + ', '.join(POSTING_FIELDS) + ') VALUES (' + ', '.join(['%s'] * len(POSTING_FIELDS)) + ') RETURNING id', parameters).fetchone()['id']
            outcome = 'inserted'
        else:
            posting_id = old['id']
            outcome = 'updated' if any(old[key] != payload[key] for key in CONTENT_FIELDS) else 'skipped_duplicates'
            conn.execute('UPDATE source_postings SET ' + ', '.join(key + '=%s' for key in POSTING_FIELDS) + ', last_seen_at=clock_timestamp() WHERE id=%s', parameters + [posting_id])
        link_posting(conn, posting_id, decision)
        refresh_canonical(conn, decision.canonical_job_id)
        if old and old['canonical_job_id'] != decision.canonical_job_id:
            refresh_canonical(conn, old['canonical_job_id'])
        return outcome


def list_source_jobs(conn, status=None, query=None, include_inactive=False, source=None):
    clauses, params = ([] if include_inactive else ["p.status NOT IN ('closed', 'dead')"]), []
    if source:
        clauses.append('s.repository=%s'); params.append(source)
    if status:
        clauses.append('p.status=%s'); params.append(status)
    rows = conn.execute(SOURCE_SELECT + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY p.posted_at DESC NULLS LAST, p.id DESC', params).fetchall()
    if query:
        rows = [row for row in rows if query.casefold() in ' '.join(str(row.get(field) or '') for field in ('company', 'title', 'location', 'term', 'keywords')).casefold()]
    return [_source_job(row) for row in rows]


def get_source_job(conn, source, external_id):
    source_id = source if isinstance(source, int) else ensure_source(conn, source)['id']
    row = conn.execute(SOURCE_SELECT + ' WHERE p.source_id=%s AND p.external_id=%s', (source_id, external_id)).fetchone()
    return _source_job(row) if row else None


def _core(row):
    return CanonicalJob.model_validate({key: row[key] for key in CORE_FIELDS})


def _job_details(stored, members):
    priority = {'open': 0, 'unknown': 1, 'closed': 2, 'dead': 3}
    ordered = sorted(members, key=lambda row: (priority[row['status']], row['posted_at'] is None, row['id']))
    refs = []
    for row in sorted(members, key=lambda row: row['id']):
        data = {key: row[key] for key in SourceJobReference.model_fields}
        data['snapshot_id'] = str(data['snapshot_id'])
        refs.append(SourceJobReference.model_validate(data))
    return JobDetails(job=_core(stored), term=ordered[0]['term'] if ordered else None, description=stored['description'],
                      keywords=JobKeywords.model_validate(stored['keywords']), sources=refs)


def list_job_details(conn, status=None, query=None, include_inactive=False, source=None, source_id=None,
                     job_ids=None, exclude_job_ids=None, include_unlinked=False):
    if job_ids is not None and not job_ids:
        return []
    clauses, params = ['i.merged_into_id IS NULL'], []
    if job_ids is not None:
        clauses.append('c.id=ANY(%s)'); params.append(job_ids)
    if exclude_job_ids:
        clauses.append('NOT (c.id=ANY(%s))'); params.append(exclude_job_ids)
    with conn.transaction():
        canonical = conn.execute('SELECT c.* FROM canonical_jobs c JOIN canonical_job_identities i ON i.id=c.id WHERE ' + ' AND '.join(clauses), params).fetchall()
        if not canonical:
            return []
        observations = conn.execute(SOURCE_SELECT + ' WHERE m.canonical_job_id=ANY(%s) ORDER BY p.id',
                                    ([row['id'] for row in canonical],)).fetchall()
    groups = {}
    for row in observations:
        groups.setdefault(row['canonical_job_id'], []).append(row)
    result = []
    for stored in canonical:
        if (not include_inactive and stored['status'] in {'closed', 'dead'}) or (status and stored['status'] != status):
            continue
        members = groups.get(stored['id'], [])
        eligible = members if include_inactive else [item for item in members if item['status'] not in {'closed', 'dead'}]
        if not members and not include_unlinked:
            continue
        if source and not any(item['source'] == source for item in eligible):
            continue
        if source_id is not None and not any(item['source_id'] == source_id for item in eligible):
            continue
        if query and query.casefold() not in ' '.join(str(item.get(field) or '') for item in members for field in ('company', 'title', 'location', 'term', 'keywords')).casefold():
            continue
        result.append(_job_details(stored, members))
    result.sort(key=lambda item: (item.job.posted_at is not None, item.job.posted_at.toordinal() if item.job.posted_at else 0, item.job.id), reverse=True)
    return result


def candidate_job_details(conn, profile_id, view='recommended', **filters):
    """Product membership over SQL-derived state; no scoring or event replay."""
    states = {row['job_id']: JobInteractionState.model_validate(row) for row in interaction_states(conn, profile_id)}
    if view == 'recommended':
        hidden = [job_id for job_id, state in states.items() if state.dismissed or state.applied]
        details = list_job_details(conn, exclude_job_ids=hidden, **filters)
    elif view in {'saved', 'applied'}:
        selected = [job_id for job_id, state in states.items()
                    if (state.saved and not state.dismissed if view == 'saved' else state.applied)]
        # Workflow history survives closure and source URL corrections/unlinking.
        details = list_job_details(conn, job_ids=selected, include_inactive=True, include_unlinked=True)
    else:
        raise ValueError('Unknown job view')
    return [(item, states.get(item.job.id) or JobInteractionState(job_id=item.job.id, saved=False, dismissed=False, applied=False))
            for item in details]


def list_jobs(conn, **filters):
    return [details.job for details in list_job_details(conn, **filters)]


def get_job(conn, job_id):
    canonical_id = root_id(conn, job_id)
    if canonical_id is None:
        return None
    row = conn.execute("SELECT c.* FROM canonical_jobs c WHERE id=%s AND status NOT IN ('closed', 'dead') AND EXISTS (SELECT 1 FROM canonical_job_sources m WHERE m.canonical_job_id=c.id)", (canonical_id,)).fetchone()
    return _core(row) if row else None


def get_job_details(conn, job_id):
    job = get_job(conn, job_id)
    if job is None:
        return None
    stored = conn.execute('SELECT * FROM canonical_jobs WHERE id=%s', (job.id,)).fetchone()
    members = conn.execute(SOURCE_SELECT + ' WHERE m.canonical_job_id=%s ORDER BY p.id', (job.id,)).fetchall()
    return _job_details(stored, members)


def reconciliation_stats(conn):
    return conn.execute("""WITH groups AS (
        SELECT m.canonical_job_id, count(*) AS members, count(DISTINCT p.source_id) AS source_count,
               count(*) FILTER (WHERE p.status NOT IN ('closed','dead')) AS active_members
        FROM source_postings p JOIN canonical_job_sources m ON m.source_posting_id=p.id GROUP BY m.canonical_job_id)
        SELECT COALESCE(sum(members),0)::bigint AS source_records, count(*) AS canonical_jobs,
               count(*) FILTER (WHERE source_count>1) AS cross_source_groups,
               COALESCE(sum(active_members),0)::bigint AS active_source_records,
               count(*) FILTER (WHERE active_members>0) AS visible_jobs,
               (COALESCE(sum(active_members),0)-count(*) FILTER (WHERE active_members>0))::bigint AS duplicate_listings_removed FROM groups""").fetchone()


def record_batch(conn, run):
    conn.execute("""INSERT INTO ingestion_batches (id, started_at, finished_at, source, status, scraped, inserted, updated, skipped_duplicates, failed, report)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET
        finished_at=EXCLUDED.finished_at, status=EXCLUDED.status, scraped=EXCLUDED.scraped, inserted=EXCLUDED.inserted,
        updated=EXCLUDED.updated, skipped_duplicates=EXCLUDED.skipped_duplicates, failed=EXCLUDED.failed, report=EXCLUDED.report""",
        (run['id'], run['started_at'], run['finished_at'], run['source'], run['status'], run['scraped'], run['inserted'], run['updated'], run['skipped_duplicates'], run['failed'], Jsonb(run)))


def get_batch(conn, batch_id):
    row = conn.execute('SELECT report FROM ingestion_batches WHERE id=%s', (batch_id,)).fetchone()
    return row['report'] if row else None


def latest_batch(conn):
    row = conn.execute('SELECT report FROM ingestion_batches ORDER BY started_at DESC LIMIT 1').fetchone()
    return row['report'] if row else None


def start_source_run(conn, source_id, batch_id):
    return conn.execute("INSERT INTO ingestion_runs (source_id, batch_id, status) VALUES (%s,%s,'running') RETURNING id", (source_id, batch_id)).fetchone()['id']


def record_source_run(conn, run_id, report, snapshot_id=None):
    conn.execute("""UPDATE ingestion_runs SET finished_at=CASE WHEN %s='running' THEN NULL ELSE clock_timestamp() END,
        status=%s, records_fetched=%s, records_parsed=%s, records_inserted=%s, records_updated=%s,
        records_failed=%s, records_skipped=%s, snapshot_id=COALESCE(%s,snapshot_id), error_summary=%s, report=%s WHERE id=%s""",
        (report['status'], report['status'], report['scraped'], report.get('parsed',0), report['inserted'], report['updated'], report['failed'], report['skipped_duplicates'],
         snapshot_id, report.get('error'), Jsonb(report), run_id))


def list_ingestion_runs(conn, source_id=None):
    return conn.execute('SELECT * FROM ingestion_runs' + (' WHERE source_id=%s' if source_id is not None else '') + ' ORDER BY started_at DESC', (source_id,) if source_id is not None else ()).fetchall()


def get_ingestion_run(conn, run_id):
    return conn.execute('SELECT * FROM ingestion_runs WHERE id=%s', (run_id,)).fetchone()


def development_profile(conn: psycopg.Connection) -> Optional[dict]:
    return conn.execute('SELECT * FROM candidate_profiles ORDER BY id LIMIT 1').fetchone()


def update_development_profile(conn: psycopg.Connection, update: CandidateProfileUpdate) -> Optional[dict]:
    # psycopg adapts these Python lists to PostgreSQL text arrays, including [].
    # The existing trigger alone owns updated_at; identity/created_at are untouched.
    return conn.execute("""UPDATE candidate_profiles SET
        target_roles=%s, skills=%s, preferred_locations=%s, preferred_terms=%s, graduation_year=%s
        WHERE id=(SELECT id FROM candidate_profiles ORDER BY id LIMIT 1)
        RETURNING *""", (update.target_roles, update.skills, update.preferred_locations,
                          update.preferred_terms, update.graduation_year)).fetchone()


def record_interaction(conn, profile_id, canonical_id, interaction_type):
    canonical_id = root_id(conn, canonical_id)
    if canonical_id is None or not conn.execute('SELECT 1 FROM canonical_jobs WHERE id=%s', (canonical_id,)).fetchone():
        raise ValueError('Unknown canonical job')
    return conn.execute('INSERT INTO job_interactions (candidate_profile_id, canonical_job_id, interaction_type) VALUES (%s,%s,%s) RETURNING *', (profile_id, canonical_id, interaction_type)).fetchone()


INTERACTION_LINEAGE = """WITH RECURSIVE lineage AS (
        SELECT DISTINCT i.id AS original_id, i.id AS job_id, i.merged_into_id
        FROM canonical_job_identities i JOIN job_interactions e ON e.canonical_job_id=i.id
        WHERE e.candidate_profile_id=%s
        UNION ALL
        SELECT l.original_id, i.id, i.merged_into_id
        FROM lineage l JOIN canonical_job_identities i ON i.id=l.merged_into_id
    ) """


RESOLVED_INTERACTIONS = INTERACTION_LINEAGE + """, events AS (
    SELECT l.job_id, e.id, e.interaction_type, e.created_at
    FROM job_interactions e JOIN lineage l ON l.original_id=e.canonical_job_id
    WHERE e.candidate_profile_id=%s AND l.merged_into_id IS NULL
), latest_signals AS (
    SELECT DISTINCT ON (job_id, signal) * FROM (
        SELECT *, CASE interaction_type WHEN 'unsave' THEN 'save'
                      WHEN 'unapplied' THEN 'applied' ELSE interaction_type END AS signal
        FROM events
    ) typed ORDER BY job_id, signal, id DESC
) """


def interaction_states(conn: psycopg.Connection, profile_id: int) -> list:
    # Latest explicit action per family wins, including across merged identities.
    # Event IDs order writes; timestamps can tie or precede a waiting transaction.
    return conn.execute(RESOLVED_INTERACTIONS + """SELECT s.job_id,
           bool_or(s.interaction_type='save') AS saved,
           bool_or(s.interaction_type='dismiss') AS dismissed,
           bool_or(s.interaction_type='applied') AS applied,
           h.last_interaction_at,
           max(s.created_at) FILTER (WHERE s.interaction_type='save') AS saved_at,
           max(s.created_at) FILTER (WHERE s.interaction_type='applied') AS applied_at,
           h.last_event_id
    FROM latest_signals s JOIN (
        SELECT job_id, max(created_at) AS last_interaction_at, max(id) AS last_event_id
        FROM events GROUP BY job_id
    ) h ON h.job_id=s.job_id
    GROUP BY s.job_id, h.last_interaction_at, h.last_event_id ORDER BY s.job_id""", (profile_id, profile_id)).fetchall()


def behavior_history(conn, profile_id):
    """Bulk-load active weighted signals; inverse events cancel save/apply evidence."""
    from .behavioral import BehaviorEvidence, INTERACTION_WEIGHTS
    rows = conn.execute(RESOLVED_INTERACTIONS + """SELECT job_id,
        array_agg(interaction_type ORDER BY interaction_type) AS interaction_types
        FROM latest_signals WHERE interaction_type=ANY(%s)
        GROUP BY job_id ORDER BY job_id""", (profile_id, profile_id, list(INTERACTION_WEIGHTS))).fetchall()
    actions = {row['job_id']: frozenset(row['interaction_types']) for row in rows}
    details = list_job_details(conn, job_ids=list(actions), include_inactive=True, include_unlinked=True)
    return [BehaviorEvidence(item, actions[item.job.id]) for item in details]


def record_interaction_with_state(conn, profile_id, canonical_id, interaction_type):
    # Use the reconciliation lock so the receipt and its state refer to one root ID.
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (RECONCILIATION_LOCK,))
        event = record_interaction(conn, profile_id, canonical_id, interaction_type)
        state = next(row for row in interaction_states(conn, profile_id) if row['job_id'] == event['canonical_job_id'])
        return {**event, 'interaction_state': state}


def save_resume_text(conn, profile_id, filename, extracted_text):
    return conn.execute('INSERT INTO resumes (candidate_profile_id, filename, extracted_text) VALUES (%s,%s,%s) RETURNING *', (profile_id, filename, extracted_text)).fetchone()


def latest_resume_metadata(conn, profile_id):
    return conn.execute('SELECT id, candidate_profile_id, filename, uploaded_at FROM resumes WHERE candidate_profile_id=%s ORDER BY uploaded_at DESC, id DESC LIMIT 1', (profile_id,)).fetchone()


def main():
    with connect() as conn:
        initialize(conn)
    print('PostgreSQL source lineage, canonical jobs, profiles, resumes, and interaction tables are ready.')


if __name__ == '__main__':
    main()
