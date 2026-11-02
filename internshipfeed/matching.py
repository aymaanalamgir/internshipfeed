"""Explainable profile-to-job rules. No I/O, learned weights, or stored scores."""
import re
import unicodedata
from typing import Iterable, List, Optional, Set, Tuple

from .models import CandidateProfile, JobDetails, JobKeywords, MatchResult


def normalize_text(value: str) -> str:
    """Fold case/accents and separators, preserving C++, C#, .NET, and Node.js."""
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return ' '.join(re.sub(r'[^\w.+#]|_', ' ', value).split())


ROLE_ALIASES = {
    'software engineering': ('software engineer', 'software developer', 'application developer', 'swe'),
    'backend engineering': ('backend', 'back end', 'server side'),
    'data engineering': ('data engineer', 'data platform', 'data pipeline', 'etl'),
    'machine learning': ('ml engineer', 'ml intern', 'applied scientist', 'ai engineer'),
    'data science': ('data scientist', 'analytics scientist'),
    'product management': ('product manager', 'technical product manager'),
    'frontend engineering': ('frontend', 'front end', 'web developer'),
    'devops': ('site reliability', 'sre', 'infrastructure', 'platform engineer'),
}
SKILL_ALIASES = {
    'postgres': 'postgresql', 'js': 'javascript', 'ts': 'typescript',
    'node': 'node.js', 'nodejs': 'node.js', 'k8s': 'kubernetes',
    'google cloud': 'gcp', 'amazon web services': 'aws', 'dotnet': '.net',
}
SKILL_LABELS = {
    'postgresql': 'PostgreSQL', 'javascript': 'JavaScript', 'typescript': 'TypeScript',
    'node.js': 'Node.js', 'kubernetes': 'Kubernetes', 'gcp': 'GCP', 'aws': 'AWS', '.net': '.NET',
}
TECHNICAL_CONCEPTS = {
    'machine learning', 'data analysis', 'distributed systems', 'rest', 'graphql',
    'ci cd', 'unit testing', 'object oriented',
}
PROVINCES = {
    'on': 'ontario', 'qc': 'quebec', 'bc': 'british columbia', 'ab': 'alberta',
    'ns': 'nova scotia', 'nb': 'new brunswick', 'mb': 'manitoba',
    'sk': 'saskatchewan', 'nl': 'newfoundland and labrador', 'pe': 'prince edward island',
}
COUNTRIES = {
    'canada': ('canada', 'canadian'),
    'usa': ('usa', 'us', 'united states', 'united states of america'),
    'uk': ('uk', 'united kingdom'),
}
ROLE_STOP_WORDS = {'intern', 'internship', 'co', 'op', 'coop', 'student', 'junior', 'senior'}
ROLE_GENERIC_WORDS = {'engineer', 'developer', 'manager', 'science'}
ROLE_STEMS = {'engineering': 'engineer', 'development': 'developer',
              'management': 'manager', 'scientist': 'science'}


def _phrase(phrase: str, text: str) -> bool:
    # Whitespace boundaries avoid substring collisions such as Java/JavaScript.
    return bool(phrase) and ' ' + phrase + ' ' in ' ' + text + ' '


def _role_families(text: str) -> Set[str]:
    return {family for family, aliases in ROLE_ALIASES.items()
            if any(_phrase(alias, text) for alias in (family,) + aliases)}


def _role_tokens(text: str) -> Set[str]:
    return {ROLE_STEMS.get(token, token) for token in text.split()
            if token not in ROLE_STOP_WORDS and not token.isdigit()}


def score_role(target_roles: Iterable[str], titles: Iterable[str]) -> Tuple[int, Optional[str]]:
    best, reason = 0, None
    titles = [(title, normalize_text(title)) for title in titles]
    for target in target_roles:
        normalized = normalize_text(target)
        if not normalized:
            continue
        families, tokens = _role_families(normalized), _role_tokens(normalized)
        for title, text in titles:
            if _phrase(normalized, text) or families & _role_families(text):
                points, rule = 40, 'phrase or role-family alias'
            else:
                overlap = tokens & _role_tokens(text)
                # "Engineering" alone is not evidence of a shared role specialty.
                anchors = overlap - ROLE_GENERIC_WORDS
                points = 30 if anchors and len(overlap) >= 2 else 15 if anchors else 0
                rule = 'role tokens: ' + ', '.join(sorted(overlap))
            if points > best:
                best = points
                reason = 'Role matches %s via "%s" (%s)' % (target, title, rule)
    return best, reason


def normalize_skill(value: str) -> str:
    key = normalize_text(value)
    return SKILL_ALIASES.get(key, key)


def role_families(titles: Iterable[str]) -> Set[str]:
    """Shared taxonomy for profile matching and behavioral metadata."""
    return {family for title in titles for family in _role_families(normalize_text(title))}


def technical_skills(keywords: JobKeywords) -> dict:
    """Normalized technical keywords only; requirements prose is not evidence."""
    values = keywords.languages + keywords.frameworks + keywords.tools_and_platforms
    values += [value for value in keywords.concepts if normalize_skill(value) in TECHNICAL_CONCEPTS]
    job_skills = {}
    for value in values:
        key = normalize_skill(value)
        if key:
            job_skills.setdefault(key, SKILL_LABELS.get(key, value.strip()))
    return job_skills


def score_skills(skills: Iterable[str], keywords: JobKeywords) -> Tuple[int, List[str], Optional[str]]:
    candidate = {normalize_skill(skill) for skill in skills if normalize_skill(skill)}
    job_skills = technical_skills(keywords)
    matched = [label for key, label in sorted(job_skills.items()) if key in candidate]
    if not matched:
        return 0, [], None
    # Integer arithmetic rounds half up rather than Python's round-to-even rule.
    points = (60 * len(matched) + len(job_skills)) // (2 * len(job_skills))
    reason = 'Matched skills: %s (%s of %s detected job skills)' % (', '.join(matched), len(matched), len(job_skills))
    return points, matched, reason


def _geography(value: str) -> Tuple[str, Set[str], Set[str], Set[str], bool]:
    text = normalize_text(value)
    text = re.sub(r'\bon site\b|\bonsite\b|\bhybrid\b', ' ', text)
    provinces = set()
    for code, name in PROVINCES.items():
        if _phrase(code, ' '.join(text.split())) or _phrase(name, ' '.join(text.split())):
            provinces.add(name)
            text = re.sub(r'(?<!\w)(?:' + code + '|' + name + r')(?!\w)', ' ', text)
    countries = set()
    for country, aliases in COUNTRIES.items():
        # Longer phrases first so "United States of America" is consumed whole.
        for alias in sorted(aliases, key=len, reverse=True):
            if _phrase(alias, ' '.join(text.split())):
                countries.add(country)
                text = re.sub(r'(?<!\w)' + alias + r'(?!\w)', ' ', text)
    remote = _phrase('remote', ' '.join(text.split()))
    city = set(text.split()) - {'remote'}
    if provinces and not countries:
        countries.add('canada')
    # Expand province aliases for exact-region comparison, without guessing cities.
    normalized = ' '.join(sorted(city | provinces | countries | ({'remote'} if remote else set())))
    return normalized, city, provinces, countries, remote


def score_location(preferences: Iterable[str], locations: Iterable[str]) -> Tuple[int, Optional[str]]:
    best, reason = 0, None
    unknown = {'', 'unknown', 'not specified', 'multiple locations', 'various', 'tbd', 'n a', 'anywhere'}
    options = [option.strip() for location in locations for option in re.split(r'[;\n|]+', location)
               if normalize_text(option) not in unknown]
    for preference in preferences:
        if normalize_text(preference) in unknown:
            continue
        key, cities, provinces, countries, remote = _geography(preference)
        for location in options:
            job_key, job_cities, job_provinces, job_countries, job_remote = _geography(location)
            # Explicit geographic conflicts cannot earn city or remote points.
            if provinces and job_provinces and not provinces & job_provinces:
                continue
            if countries and job_countries and not countries & job_countries:
                continue
            if remote:
                matches = job_remote and cities <= job_cities and provinces <= job_provinces and countries <= job_countries
                points, rule = (20 if matches else 0), 'explicit remote location'
            elif key and key == job_key and (cities or provinces):
                points, rule = 20, 'exact city or region'
            elif cities and cities <= job_cities:
                points, rule = 20, 'city'
            elif provinces & job_provinces:
                points, rule = 15, 'province overlap'
            elif countries & job_countries:
                points, rule = 8, 'country overlap only'
            else:
                points, rule = 0, ''
            if points > best:
                best = points
                reason = 'Preferred location matches %s via "%s" (%s)' % (preference, location, rule)
    return best, reason


def location_keys(locations: Iterable[str]) -> Set[str]:
    """Explicit normalized locations, without new geographic inference."""
    unknown = {'', 'unknown', 'not specified', 'multiple locations', 'various', 'tbd', 'n a', 'anywhere'}
    return {_geography(option)[0] for location in locations for option in re.split(r'[;\n|]+', location)
            if normalize_text(option) not in unknown and _geography(option)[0]}


TERM_PATTERN = re.compile(
    r'\b(?P<year_first>(?:19|20|21|22)\d{2})\s+(?P<season_after>summer|winter|fall|autumn|spring)\b'
    r'|\b(?P<season>summer|winter|fall|autumn|spring)\b(?:\s+(?P<year>(?:19|20|21|22)\d{2})\b)?'
    r'|\b(?P<year_only>(?:19|20|21|22)\d{2})\b'
)


def _terms(value: str) -> List[Tuple[Optional[str], Optional[str]]]:
    result = []
    for match in TERM_PATTERN.finditer(normalize_text(value)):
        season = match['season_after'] or match['season']
        result.append(('fall' if season == 'autumn' else season,
                       match['year_first'] or match['year'] or match['year_only']))
    return result


def term_keys(terms: Iterable[str]) -> Set[str]:
    """Behavior uses complete season/year evidence, never posting dates."""
    return {season + ' ' + year for value in terms for season, year in _terms(value) if season and year}


def score_term(preferences: Iterable[str], terms: Iterable[str]) -> Tuple[int, Optional[str]]:
    best, reason = 0, None
    evidence = [(value, _terms(value)) for value in terms]
    for preference in preferences:
        for season, year in _terms(preference):
            for value, parsed in evidence:
                for job_season, job_year in parsed:
                    if season and job_season and season != job_season or year and job_year and year != job_year:
                        continue
                    if season and year and season == job_season and year == job_year:
                        points, rule = 10, 'season and year'
                    elif season and season == job_season:
                        points, rule = 6, 'season only; year missing'
                    elif year and year == job_year:
                        points, rule = 4, 'year only; season missing'
                    else:
                        points, rule = 0, ''
                    if points > best:
                        best = points
                        reason = 'Preferred term matches %s via "%s" (%s)' % (preference, value, rule)
    return best, reason


def score_job(profile: CandidateProfile, details: JobDetails) -> MatchResult:
    sources = [source for source in details.sources if source.status not in {'closed', 'dead'}]
    role, role_reason = score_role(profile.target_roles, [details.job.title] + [source.title for source in sources])
    skills, matched, skill_reason = score_skills(profile.skills, details.keywords)
    location, location_reason = score_location(profile.preferred_locations, [details.job.location] + [source.location for source in sources])
    # Structured terms win. Titles supply evidence only when that observation has no term.
    terms = [details.term] if details.term else [details.job.title]
    terms += [source.term or source.title for source in sources]
    term, term_reason = score_term(profile.preferred_terms, terms)
    return MatchResult(job=details.job, score=min(100, max(0, role + skills + location + term)),
                       role_score=role, skills_score=skills, location_score=location, term_score=term,
                       matched_skills=matched,
                       reasons=[reason for reason in (role_reason, skill_reason, location_reason, term_reason) if reason])


def rank_jobs(profile: CandidateProfile, jobs: Iterable[JobDetails], min_score: int = 0) -> List[MatchResult]:
    results = [score_job(profile, details) for details in jobs if details.job.status not in {'closed', 'dead'}]
    results = [result for result in results if result.score >= min_score]
    results.sort(key=lambda result: (result.score, result.job.posted_at is not None,
                                    result.job.posted_at.toordinal() if result.job.posted_at else 0,
                                    result.job.id), reverse=True)
    return results
