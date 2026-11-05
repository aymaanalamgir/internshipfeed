"""Validate opaque catalog cursors for descending posting dates and canonical IDs."""
import base64
import json
from datetime import date


def encode_cursor(posted_at, job_id):
    payload = json.dumps([posted_at.isoformat() if posted_at else None, job_id], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip('=')


def decode_cursor(value):
    if value is None:
        return None
    try:
        payload = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if not isinstance(payload, list) or len(payload) != 2:
            raise ValueError()
        posted_at, job_id = payload
        if type(job_id) is not int or not 0 < job_id <= 9223372036854775807:
            raise ValueError()
        if posted_at is not None:
            if not isinstance(posted_at, str):
                raise ValueError()
            parsed = date.fromisoformat(posted_at)
            if parsed.isoformat() != posted_at:
                raise ValueError()
            posted_at = parsed
        return posted_at, job_id
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('Invalid pagination cursor') from None
