"""Source registry: each repository supplies a parser, not a separate job model."""
from dataclasses import dataclass
from typing import Callable, List

from ..records import SourceJobRecord
from ..source import parse_readme
from .simplify import parse_simplify


@dataclass(frozen=True)
class JobSource:
    key: str
    name: str
    url: str
    readme_url: str
    parse: Callable[[str], List[SourceJobRecord]]


def parse_canadian(markdown):
    return [SourceJobRecord.model_validate(row) for row in parse_readme(markdown)]


CANADIAN = JobSource(
    key="canadian-tech", name="Canadian Tech Internships",
    url="https://github.com/negarprh/Canadian-Tech-Internships-2027",
    readme_url="https://raw.githubusercontent.com/negarprh/Canadian-Tech-Internships-2027/main/README.md",
    parse=parse_canadian,
)
# The supplied Summer2026 repository URL redirects to Summer2027 upstream.
SIMPLIFY = JobSource(
    key="simplify", name="Simplify Internships",
    url="https://github.com/SimplifyJobs/Summer2027-Internships",
    readme_url="https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
    parse=parse_simplify,
)
SIMPLIFY_OFF_SEASON = JobSource(
    key="simplify-off-season", name="Simplify Off-Season Internships",
    url=SIMPLIFY.url,
    readme_url="https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README-Off-Season.md",
    parse=parse_simplify,
)
SOURCES = (CANADIAN, SIMPLIFY, SIMPLIFY_OFF_SEASON)


def get_source(key):
    for source in SOURCES:
        if source.key == key:
            return source
    raise ValueError("Unknown job source: %s" % key)
