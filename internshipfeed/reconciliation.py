"""URL-based reconciliation, independent of source-specific parsing and identities."""
import hashlib
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


def reconcile(conn, payload, previous=None):
    """Return a canonical ID, preserving source identity and recording URL aliases.

    Caller holds RECONCILIATION_LOCK in its transaction. Only the current URL and
    a successfully read description's resolved URL are evidence of equivalence.
    A URL correction can detach one source record without dragging its old peers.
    """
    previous_id = root_id(conn, previous.get('canonical_job_id')) if previous else None
    if not payload.get('url') and previous_id is not None:
        return previous_id  # a closed marker often removes the URL
    keys = []
    primary = matching_url(payload.get('url') or payload.get('last_known_url'))
    if primary:
        keys.append(primary)
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
        for other_id in sorted(owners - {canonical_id}):
            conn.execute('UPDATE source_jobs SET canonical_job_id = %s WHERE canonical_job_id = %s', (canonical_id, other_id))
            conn.execute('UPDATE canonical_job_urls SET canonical_job_id = %s WHERE canonical_job_id = %s', (canonical_id, other_id))
            conn.execute('UPDATE canonical_job_identities SET merged_into_id = %s WHERE id = %s', (canonical_id, other_id))
    elif not keys and previous_id is not None:
        canonical_id = previous_id
    else:
        canonical_id = conn.execute('INSERT INTO canonical_job_identities DEFAULT VALUES RETURNING id').fetchone()['id']
    for url in keys:
        digest = hashlib.sha256(url.encode()).hexdigest()
        conn.execute('INSERT INTO canonical_job_urls (url_key, canonical_url, canonical_job_id) VALUES (%s, %s, %s) '
                     'ON CONFLICT (url_key) DO UPDATE SET canonical_job_id = EXCLUDED.canonical_job_id', (digest, url, canonical_id))
    return canonical_id


def backfill(conn):
    """Attach existing rows while retaining every original source ID and observation."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (RECONCILIATION_LOCK,))
    for row in conn.execute('SELECT * FROM source_jobs WHERE canonical_job_id IS NULL ORDER BY id').fetchall():
        canonical_id = reconcile(conn, row, previous=row)
        conn.execute('UPDATE source_jobs SET canonical_job_id = %s WHERE id = %s', (canonical_id, row['id']))
    conn.execute('ALTER TABLE source_jobs ALTER COLUMN canonical_job_id SET NOT NULL')
