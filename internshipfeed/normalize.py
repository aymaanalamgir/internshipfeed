"""Common job schema and deterministic within-source identity."""
import hashlib
import re
from datetime import datetime

from .models import JobListing


def normalize(record, source_url, snapshot_id):
    terms = re.findall(r"\b(Winter|Summer|Fall|Autumn|Spring)\s+(20\d{2})\b", record["role"], re.I)
    terms = list(dict.fromkeys("%s %s" % (season.title(), year) for season, year in terms))
    raw_date = record["date_posted_raw"]
    try:
        posted_at = datetime.strptime(raw_date, "%b %d, %Y").date()
    except ValueError:
        posted_at = None
    # The source removes URLs on closure. Do not include URL/status in identity.
    identity = [source_url, record["company"].casefold(), record["role"].casefold(),
                record["location"].casefold(), posted_at.isoformat() if posted_at else raw_date]
    external_id = hashlib.sha256("\0".join(identity).encode()).hexdigest()
    return JobListing(
        company=record["company"], title=record["role"], location=record["location"],
        term="; ".join(terms) or None, status=record["status"], url=record["raw_url"],
        posted_at=posted_at, source=source_url, external_id=external_id,
        snapshot_id=snapshot_id, source_line=record["source_line"],
    )
