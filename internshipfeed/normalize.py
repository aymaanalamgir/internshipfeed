"""One source-independent transformation into the shared JobListing contract."""
import hashlib
import re
from datetime import datetime, timedelta

from .models import JobListing
from .records import SourceJobRecord


AGE_UNIT_DAYS = {"h": 0, "d": 1, "w": 7, "m": 30, "mo": 30}


def age_to_date(raw_age, observed_on):
    """Estimate a posting date from an age such as ``2d`` seen on the scrape date."""
    match = re.fullmatch(r"(\d+)\s*(h|d|w|mo|m)", raw_age.strip(), re.I)
    if not match:
        return None
    try:
        return observed_on - timedelta(days=int(match[1]) * AGE_UNIT_DAYS[match[2].lower()])
    except OverflowError:
        return None


def normalize(record, source_url, snapshot_id, observed_on=None):
    record = SourceJobRecord.model_validate(record)
    terms = re.findall(r"\b(Winter|Summer|Fall|Autumn|Spring)\s+(20\d{2})\b", record.title, re.I)
    terms = list(dict.fromkeys("%s %s" % (season.title(), year) for season, year in terms))
    posted_at = None
    if record.date_kind == "exact":
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
            try:
                posted_at = datetime.strptime(record.posted_at_raw, fmt).date()
                break
            except ValueError:
                continue
    elif record.date_kind == "relative" and observed_on is not None:
        # Simplify publishes ages, so the posting date is the scrape date minus that age.
        posted_at = age_to_date(record.posted_at_raw, observed_on)
    # Relative ages shift daily and must never change identity on reruns.
    raw_date = record.posted_at_raw if record.date_kind == "exact" else ""
    identity = [source_url, record.company.casefold(), record.title.casefold(),
                record.location.casefold(),
                posted_at.isoformat() if posted_at and record.date_kind == "exact" else raw_date]
    external_id = record.external_id or hashlib.sha256("\0".join(identity).encode()).hexdigest()
    return JobListing(
        company=record.company, title=record.title, location=record.location,
        term="; ".join(terms) or record.term, status=record.status, url=record.url,
        posted_at=posted_at, source=source_url, external_id=external_id,
        snapshot_id=str(snapshot_id), source_line=record.source_line,
        raw_company=record.raw_company if record.raw_company is not None else record.company,
        raw_title=record.raw_title if record.raw_title is not None else record.title,
        raw_location=record.raw_location if record.raw_location is not None else record.location,
        raw_url=record.raw_url if record.raw_url is not None else record.url,
        raw_posted_at=record.raw_posted_at if record.raw_posted_at is not None else record.posted_at_raw,
        raw_row=record.raw_row,
    )
