"""Persist application progression while keeping interaction history and canonical identity intact."""
from collections import defaultdict
from typing import get_args

from .models import ApplicationStatus
from .reconciliation import RECONCILIATION_LOCK, root_id


def record_application_event(conn, profile_id, canonical_id, interaction_type, timestamp):
    if interaction_type == 'applied':
        conn.execute('''INSERT INTO job_applications
            (candidate_profile_id, canonical_job_id, applied_at, status_updated_at, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (candidate_profile_id, canonical_job_id) DO NOTHING''',
            (profile_id, canonical_id, timestamp, timestamp, timestamp, timestamp))
    elif interaction_type == 'unapplied':
        conn.execute('DELETE FROM job_applications WHERE candidate_profile_id=%s AND canonical_job_id=%s', (profile_id, canonical_id))


def list_applications(conn, profile_id, status=None):
    if status is not None and status not in get_args(ApplicationStatus):
        raise ValueError('Unsupported application status')
    return conn.execute('''SELECT * FROM job_applications WHERE candidate_profile_id=%s'''
        + (' AND status=%s' if status is not None else '') + ' ORDER BY updated_at DESC, id DESC',
        (profile_id, status) if status is not None else (profile_id,)).fetchall()


def update_application(conn, profile_id, job_id, status):
    if status not in get_args(ApplicationStatus):
        raise ValueError('Unsupported application status')
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (RECONCILIATION_LOCK,))
        canonical_id = root_id(conn, job_id)
        row = conn.execute('SELECT * FROM job_applications WHERE candidate_profile_id=%s AND canonical_job_id=%s FOR UPDATE',
                           (profile_id, canonical_id)).fetchone()
        if row is None:
            return None
        if row['status'] == status:
            return row
        return conn.execute('''WITH moment AS (SELECT clock_timestamp() AS ts)
            UPDATE job_applications SET status=%s, status_updated_at=moment.ts, updated_at=moment.ts
            FROM moment WHERE id=%s RETURNING job_applications.*''', (status, row['id'])).fetchone()


def merge_application_records(conn, canonical_id, other_id):
    groups = defaultdict(list)
    for row in conn.execute('''SELECT * FROM job_applications WHERE canonical_job_id=ANY(%s)
                              ORDER BY candidate_profile_id, id FOR UPDATE''', ([canonical_id, other_id],)):
        groups[row['candidate_profile_id']].append(row)
    for profile_id, rows in groups.items():
        latest = conn.execute('''WITH RECURSIVE aliases AS (
                SELECT id FROM canonical_job_identities WHERE id=%s
                UNION ALL SELECT i.id FROM canonical_job_identities i JOIN aliases a ON i.merged_into_id=a.id
            ) SELECT e.interaction_type FROM job_interactions e
            WHERE e.candidate_profile_id=%s AND e.canonical_job_id IN (SELECT id FROM aliases)
              AND e.interaction_type IN ('applied', 'unapplied') ORDER BY e.id DESC LIMIT 1''', (canonical_id, profile_id)).fetchone()
        if latest is None or latest['interaction_type'] == 'unapplied':
            conn.execute('DELETE FROM job_applications WHERE id=ANY(%s)', ([row['id'] for row in rows],))
            continue
        keeper = next((row for row in rows if row['canonical_job_id'] == canonical_id), rows[0])
        winner = max(rows, key=lambda row: (row['status_updated_at'], row['updated_at'], row['id']))
        conn.execute('DELETE FROM job_applications WHERE id=ANY(%s)', ([row['id'] for row in rows if row['id'] != keeper['id']],))
        conn.execute('''UPDATE job_applications SET canonical_job_id=%s, status=%s, applied_at=%s,
            status_updated_at=%s, created_at=%s, updated_at=%s WHERE id=%s''',
            (canonical_id, winner['status'], min(row['applied_at'] for row in rows), winner['status_updated_at'],
             min(row['created_at'] for row in rows), max(row['updated_at'] for row in rows), keeper['id']))
