"""One source-independent transformation into the shared JobListing contract."""
import hashlib
import re
from datetime import datetime

from .models import JobListing
from .records import SourceJobRecord


def normalize(record, source_url, snapshot_id):
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
    # Relative ages are not posting timestamps and must never change identity on reruns.
    raw_date = record.posted_at_raw if record.date_kind == "exact" else ""
    identity = [source_url, record.company.casefold(), record.title.casefold(),
                record.location.casefold(), posted_at.isoformat() if posted_at else raw_date]
    external_id = record.external_id or hashlib.sha256("\0".join(identity).encode()).hexdigest()
    return JobListing(
        company=record.company, title=record.title, location=record.location,
        term="; ".join(terms) or record.term, status=record.status, url=record.url,
        posted_at=posted_at, source=source_url, external_id=external_id,
        snapshot_id=snapshot_id, source_line=record.source_line,
    )
