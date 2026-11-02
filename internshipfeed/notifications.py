"""Transactional email rendering and the official Resend SDK boundary."""
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from email.utils import getaddresses
from html import escape
from typing import Iterable, Mapping, Optional, Tuple
from urllib.parse import urlparse
from uuid import UUID

import resend

from .models import RankedMatchResult


class EmailError(RuntimeError):
    pass


def validate_channel():
    if os.environ.get('DAILY_NOTIFICATION_CHANNEL', 'email').strip() != 'email':
        raise ValueError('DAILY_NOTIFICATION_CHANNEL must be email')


@dataclass(frozen=True)
class EmailConfig:
    api_key: str = field(repr=False)
    from_address: str = field(repr=False)
    to_address: str = field(repr=False)

    @classmethod
    def from_env(cls):
        validate_channel()
        names = ('RESEND_API_KEY', 'DAILY_EMAIL_FROM', 'DAILY_EMAIL_TO')
        values = [os.environ.get(name, '').strip() for name in names]
        missing = [name for name, value in zip(names, values) if not value]
        if missing:
            raise ValueError('Missing email settings: ' + ', '.join(missing))
        for name, value in zip(names[1:], values[1:]):
            addresses = getaddresses([value])
            address = addresses[0][1] if len(addresses) == 1 else ''
            if any(char in value for char in '\r\n') or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', address):
                raise ValueError(name + ' must contain one valid email address')
            if name == 'DAILY_EMAIL_TO' and value != address:
                raise ValueError(name + ' must contain one email address without a display name')
        return cls(*values)


@dataclass(frozen=True)
class EmailReceipt:
    message_id: str


@dataclass(frozen=True)
class EmailDigest:
    subject: str
    html: str
    text: str
    job_ids: Tuple[int, ...]


def safe_url(url):
    try:
        parsed = urlparse(url or '')
        return bool(url and parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username
                    and not parsed.password and not any(char.isspace() or ord(char) < 32 for char in url))
    except ValueError:
        return False


def _line(value):
    return ' '.join(value.split())


def format_email(matches: Iterable[RankedMatchResult], top_n: int, terms: Optional[Mapping[int, Optional[str]]] = None,
                 app_base_url: Optional[str] = None) -> EmailDigest:
    cards, plain, ids = [], [], []
    for match in matches:
        job = match.job
        if len(ids) >= top_n:
            break
        if job.id in ids or not safe_url(job.application_url):
            continue
        company, title = _line(job.company), _line(job.title)
        metadata = ' · '.join(filter(None, [_line(job.location), _line((terms or {}).get(job.id) or '')]))
        skills = [_line(skill) for skill in match.matched_skills]
        tags = ' '.join('<span style="display:inline-block;border:1px solid #dcd8cf;padding:3px 7px;margin:0 4px 4px 0;font:11px monospace;color:#56534c">%s</span>' % escape(skill) for skill in skills)
        cards.append('''<tr><td style="padding:24px 0;border-top:1px solid #dcd8cf">
<p style="margin:0 0 12px;color:#a94f2b;font-size:12px">%s%% match</p>
<p style="margin:0 0 4px;font-size:13px">%s</p><h2 style="margin:0 0 8px;font-size:18px;font-weight:500">%s</h2>
<p style="margin:0 0 12px;color:#726e65;font-size:12px">%s</p><div>%s</div>
<a href="%s" style="display:inline-block;margin-top:12px;color:#a94f2b;font-size:13px">Apply →</a></td></tr>''' %
                     (match.final_score, escape(company), escape(title), escape(metadata), tags, escape(job.application_url, quote=True)))
        plain.append('%s%% match\n%s\n%s\n%s%s\nApply: %s' % (match.final_score, company, title, metadata,
                     '\n' + ' · '.join(skills) if skills else '', job.application_url))
        ids.append(job.id)
    if not ids:
        return EmailDigest('', '', '', ())
    count = len(ids)
    subject = 'InternshipFeed — %s new %s' % (count, 'match' if count == 1 else 'matches')
    intro = '%s new %s matched your profile.' % (count, 'internship' if count == 1 else 'internships')
    footer_html, footer_text = '', ''
    if safe_url(app_base_url):
        footer_html = '<p style="font-size:12px"><a style="color:#726e65" href="%s">Open InternshipFeed</a></p>' % escape(app_base_url, quote=True)
        footer_text = '\n\nOpen InternshipFeed: ' + app_base_url
    markup = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title></head>
<body style="margin:0;background:#faf8f3;color:#24231f;font-family:Arial,sans-serif"><main style="max-width:560px;margin:0 auto;padding:32px 24px">
<p style="margin:0 0 24px;font-size:13px;letter-spacing:.04em">InternshipFeed</p><h1 style="font-size:21px;font-weight:500;margin:0 0 24px">%s</h1>
<table role="presentation" style="width:100%%;border-collapse:collapse"><tbody>%s</tbody></table>%s</main></body></html>''' % (escape(subject), escape(intro), ''.join(cards), footer_html)
    return EmailDigest(subject, markup, intro + '\n\n' + '\n\n'.join(plain) + footer_text, tuple(ids))


def send_email(digest: EmailDigest, config: Optional[EmailConfig] = None) -> EmailReceipt:
    config = config or EmailConfig.from_env()
    if not digest.job_ids or not digest.text or not digest.html:
        raise EmailError('Cannot send an empty digest')
    params = {'from': config.from_address, 'to': [config.to_address], 'subject': digest.subject,
              'html': digest.html, 'text': digest.text}
    # Stable across retry of the exact payload. Resend retains keys for 24 hours;
    # PostgreSQL supplies permanent canonical-level deduplication after acceptance.
    key = hashlib.sha256(json.dumps([params, digest.job_ids], sort_keys=True).encode()).hexdigest()
    previous_key = resend.api_key
    try:
        resend.api_key = config.api_key
        response = resend.Emails.send(params, {'idempotency_key': 'internshipfeed-' + key})
        message_id = response.get('id') if isinstance(response, dict) else None
        UUID(message_id)
    except Exception:
        # Provider errors can contain addresses or credentials. Never echo them.
        raise EmailError('Resend did not confirm email acceptance; check the provider before retrying.') from None
    finally:
        resend.api_key = previous_key
    return EmailReceipt(message_id)
