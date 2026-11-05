"""Resolve opaque anonymous browser identities in PostgreSQL and protect same-origin user writes."""
import os
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException

from . import db

COOKIE_NAME = 'internshipfeed_session'
COOKIE_MAX_AGE = 180 * 24 * 60 * 60


def resolve_candidate(conn, cookie):
    try:
        user_id = UUID(cookie)
        valid = user_id.version == 4 and str(user_id) == cookie
    except (ValueError, TypeError, AttributeError):
        valid = False
    if valid:
        candidate = db.candidate_for_user(conn, user_id)
        if candidate:
            return user_id, candidate, False
    user_id, candidate = db.create_anonymous_candidate(conn)
    return user_id, candidate, True


def secure_cookie(request):
    hosted = os.environ.get('VERCEL') == '1' and os.environ.get('VERCEL_ENV') != 'development'
    return hosted or request.url.scheme == 'https'


def _origin(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        return None


def user_mutation(request):
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-origin actions are not allowed.')
    origin = request.headers.get('origin')
    if not origin:
        return
    expected = str(request.base_url).rstrip('/')
    if os.environ.get('VERCEL') == '1' and request.headers.get('x-forwarded-host'):
        host = request.headers['x-forwarded-host']
        scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
        expected = scheme + '://' + host
    if _origin(origin) is None or _origin(origin) != _origin(expected):
        raise HTTPException(403, 'Cross-origin actions are not allowed.')
