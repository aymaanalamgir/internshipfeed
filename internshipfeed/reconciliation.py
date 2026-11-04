"""URL-based reconciliation, independent of source-specific parsing and identities."""
import hashlib
from dataclasses import dataclass
from psycopg.types.json import Jsonb
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Serialize mapping changes, including merges and source URL corrections. Acquired
# before source row locks so ingestion and backfills share the same lock order.
RECONCILIATION_LOCK = 78112029
TRACKING_KEYS = frozenset({
    'ref', 'referrer', 'source', 'src', 'gh_src', 'lever-source', 'lever-origin',
    'fbclid', 'gclid', 'msclkid', 'mc_cid', 'mc_eid',
})
SECTION_ANCHORS = frozenset({'apply', 'application', 'job-description', 'description', 'main', 'top'})
UNRESERVED = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')


def canonicalize_url(value):
    """Remove transport/tracking differences without erasing job-identifying data.

    No network calls, title matching, path lowercasing, or arbitrary query removal.
    Hash routing is retained; only known page-section anchors are discarded.
    """
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        if parts.scheme.lower() not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return None
        host = parts.hostname.lower().encode('idna').decode('ascii')
        if ':' in host:
            host = '[' + host + ']'
        port = parts.port
        if port is not None and not (parts.scheme.lower() == 'http' and port == 80 or parts.scheme.lower() == 'https' and port == 443):
            host += ':' + str(port)
        def normalize_escape(match):
            character = chr(int(match[1], 16))
            return character if character in UNRESERVED else '%' + match[1].upper()
        path = re.sub(r'%([0-9a-fA-F]{2})', normalize_escape, parts.path).rstrip('/') or '/'
        pairs = [(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.lower().startswith('utm_') and key.lower() not in TRACKING_KEYS]
        # Sort keys, but retain the order of duplicate parameters (first-value semantics).
        query = urlencode(sorted(pairs, key=lambda pair: pair[0]))
        fragment = '' if parts.fragment.lower() in SECTION_ANCHORS else parts.fragment
        return urlunsplit(('https', host, path, query, fragment))
    except (ValueError, UnicodeError):
        return None


def matching_url(value):
    """Exclude obvious career/search landing pages without a job identifier."""
    normalized = canonicalize_url(value)
    if normalized is None:
        return None
    parts = urlsplit(normalized)
    final_segment = parts.path.rstrip('/').rsplit('/', 1)[-1].lower()
    generic = parts.path == '/' or final_segment in {'careers', 'jobs', 'search', 'openings', 'positions'}
    identifiers = {'id', 'gh_jid', 'job_id', 'jobid', 'req_id', 'reqid', 'requisitionid',
                   'requisition_id', 'jid', 'positionid', 'postingid'}
    has_identifier = any(key.lower() in identifiers and item for key, item in parse_qsl(parts.query))
    hash_route = '/' in parts.fragment or '?' in parts.fragment
    if generic and not has_identifier and not hash_route:
        return None
    return normalized


def root_id(conn, canonical_id):
    while canonical_id is not None:
        row = conn.execute('SELECT merged_into_id FROM canonical_job_identities WHERE id = %s', (canonical_id,)).fetchone()
        if row is None:
            return None
        if row['merged_into_id'] is None:
            return canonical_id
        canonical_id = row['merged_into_id']
    return None


@dataclass(frozen=True)
class MatchDecision:
    canonical_job_id: int
    match_method: str
    match_confidence: float = 1.0
    matched_url: str = None


def ensure_canonical(conn, canonical_id, payload):
    conn.execute("""INSERT INTO canonical_jobs
        (id, company, title, location, application_url, posted_at, status, first_seen_at, last_seen_at, created_at)
        SELECT id, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()), created_at
        FROM canonical_job_identities WHERE id = %s
        ON CONFLICT (id) DO NOTHING""",
        (payload['company'], payload['title'], payload['location'], payload.get('url'), payload.get('posted_at'),
         payload['status'], payload.get('first_seen_at'), payload.get('last_seen_at'), canonical_id))


def reconcile(conn, payload, previous=None):
    """Resolve URL evidence to a canonical identity; caller owns the write lock."""
    previous_id = root_id(conn, previous.get('canonical_job_id')) if previous else None
    if not payload.get('url') and previous_id is not None:
        ensure_canonical(conn, previous_id, payload)
        return MatchDecision(previous_id, previous.get('match_method') or 'source_identity',
                             float(previous['match_confidence']) if previous.get('match_confidence') is not None else 1.0, previous.get('matched_url'))
    keys = []
    primary = matching_url(payload.get('url') or payload.get('last_known_url'))
    if primary:
        keys.append(primary)
    resolved = None
    if payload.get('url') and payload.get('enrichment_status') == 'success' and payload.get('description'):
        resolved = matching_url(payload.get('resolved_url'))
        if resolved and resolved not in keys:
            keys.append(resolved)
    owners = set()
    for url in keys:
        digest = hashlib.sha256(url.encode()).hexdigest()
        row = conn.execute('SELECT canonical_job_id, canonical_url FROM canonical_job_urls WHERE url_key = %s', (digest,)).fetchone()
        if row:
            if row['canonical_url'] != url:
                raise ValueError('URL identity hash collision')
            owners.add(root_id(conn, row['canonical_job_id']))
    if owners:
        canonical_id = min(owners)
    elif not keys and previous_id is not None:
        canonical_id = previous_id
    else:
        canonical_id = conn.execute('INSERT INTO canonical_job_identities DEFAULT VALUES RETURNING id').fetchone()['id']
    ensure_canonical(conn, canonical_id, payload)
    for other_id in sorted(owners - {canonical_id}):
        conn.execute('UPDATE canonical_job_sources SET canonical_job_id = %s WHERE canonical_job_id = %s', (canonical_id, other_id))
        conn.execute('UPDATE canonical_job_urls SET canonical_job_id = %s WHERE canonical_job_id = %s', (canonical_id, other_id))
        conn.execute('UPDATE canonical_job_identities SET merged_into_id = %s WHERE id = %s', (canonical_id, other_id))
        from .applications import merge_application_records
        merge_application_records(conn, canonical_id, other_id)
    for url in keys:
        digest = hashlib.sha256(url.encode()).hexdigest()
        conn.execute('INSERT INTO canonical_job_urls (url_key, canonical_url, canonical_job_id) VALUES (%s, %s, %s) '
                     'ON CONFLICT (url_key) DO UPDATE SET canonical_job_id = EXCLUDED.canonical_job_id', (digest, url, canonical_id))
    method = 'resolved_application_url' if resolved and resolved != primary else 'exact_application_url' if primary else 'source_identity'
    return MatchDecision(canonical_id, method, 1.0, resolved or primary)


def link_posting(conn, posting_id, decision):
    conn.execute("""INSERT INTO canonical_job_sources
        (canonical_job_id, source_posting_id, match_method, match_confidence, matched_url)
        VALUES (%s, %s, %s, %s, %s) ON CONFLICT (source_posting_id) DO UPDATE SET
        canonical_job_id = EXCLUDED.canonical_job_id, match_method = EXCLUDED.match_method,
        match_confidence = EXCLUDED.match_confidence, matched_url = EXCLUDED.matched_url,
        created_at = CASE WHEN (canonical_job_sources.canonical_job_id, canonical_job_sources.match_method,
                                canonical_job_sources.match_confidence, canonical_job_sources.matched_url)
                              IS DISTINCT FROM (EXCLUDED.canonical_job_id, EXCLUDED.match_method,
                                                EXCLUDED.match_confidence, EXCLUDED.matched_url)
                          THEN clock_timestamp() ELSE canonical_job_sources.created_at END""",
        (decision.canonical_job_id, posting_id, decision.match_method, decision.match_confidence, decision.matched_url))


def refresh_canonical(conn, canonical_id):
    rows = conn.execute("""SELECT p.*, p.application_url AS url FROM source_postings p
        JOIN canonical_job_sources m ON m.source_posting_id = p.id
        WHERE m.canonical_job_id = %s ORDER BY
          CASE p.status WHEN 'open' THEN 0 WHEN 'unknown' THEN 1 WHEN 'closed' THEN 2 ELSE 3 END,
          p.posted_at IS NULL, p.id""", (canonical_id,)).fetchall()
    if not rows:
        return
    representative = rows[0]
    descriptions = sorted((row for row in rows if row['description']),
                          key=lambda row: (row['status'] in {'closed', 'dead'},
                                           -(row['enriched_at'].timestamp() if row['enriched_at'] else 0), row['id']))
    keyword_names = ('languages', 'frameworks', 'tools_and_platforms', 'concepts', 'requirements')
    keywords = {key: list(dict.fromkeys(value for row in rows for value in row['keywords'].get(key, []))) for key in keyword_names}
    dates = [row['posted_at'] for row in rows if row['posted_at'] is not None]
    data = {key: representative[key] for key in ('company', 'title', 'location', 'status')}
    data.update(application_url=representative['application_url'], posted_at=min(dates) if dates else None,
                description=descriptions[0]['description'] if descriptions else None, keywords=keywords,
                first_seen_at=min(row['first_seen_at'] for row in rows), last_seen_at=max(row['last_seen_at'] for row in rows))
    old = conn.execute('SELECT * FROM canonical_jobs WHERE id = %s', (canonical_id,)).fetchone()
    content = ('company', 'title', 'location', 'application_url', 'posted_at', 'status', 'description', 'keywords')
    changed = any(data[key] != old[key] for key in content)
    fields = list(data)
    values = [Jsonb(data[key]) if key == 'keywords' else data[key] for key in fields]
    conn.execute('UPDATE canonical_jobs SET ' + ', '.join(key + ' = %s' for key in fields) +
                 (', updated_at = clock_timestamp()' if changed else '') + ' WHERE id = %s', values + [canonical_id])


def backfill(conn):
    """Complete pending mappings and materialize existing canonical metadata."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (RECONCILIATION_LOCK,))
    rows = conn.execute("""SELECT p.*, p.application_url AS url, m.canonical_job_id,
        m.match_method, m.match_confidence, m.matched_url FROM source_postings p
        LEFT JOIN canonical_job_sources m ON m.source_posting_id = p.id ORDER BY p.id""").fetchall()
    for row in rows:
        decision = reconcile(conn, row, previous=row)
        link_posting(conn, row['id'], decision)
    for row in conn.execute('SELECT DISTINCT canonical_job_id FROM canonical_job_sources').fetchall():
        refresh_canonical(conn, row['canonical_job_id'])
