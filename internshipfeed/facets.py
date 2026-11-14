"""Normalize raw location and term cells into canonical, validated filter values.

Sources publish free text such as ``Toronto, ON, Canada / NYC`` or
``Winter 2027, Spring 2027``. Filters need discrete values, so each raw cell
becomes a list: a job with several cities or terms appears under each of them.
"""
import re
import unicodedata

# Bump when the rules below change; startup then recomputes every stored job once.
RULES_VERSION = 1

US_STATES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
    'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'DC': 'District of Columbia',
    'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois',
    'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana',
    'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota',
    'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada',
    'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
    'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon',
    'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota',
    'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia',
    'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
}
CA_PROVINCES = {
    'AB': 'Alberta', 'BC': 'British Columbia', 'MB': 'Manitoba', 'NB': 'New Brunswick',
    'NL': 'Newfoundland and Labrador', 'NS': 'Nova Scotia', 'NT': 'Northwest Territories',
    'NU': 'Nunavut', 'ON': 'Ontario', 'PE': 'Prince Edward Island', 'QC': 'Quebec',
    'SK': 'Saskatchewan', 'YT': 'Yukon',
}
# US and Canadian codes do not collide, so "City, XX" is unambiguous without a country.
REGION_CODES = {**US_STATES, **CA_PROVINCES}
REGION_BY_NAME = {name.casefold(): code for code, name in REGION_CODES.items()}
NORTH_AMERICA = {'canada', 'can', 'ca', 'usa', 'us', 'u.s.', 'u.s.a.', 'united states',
                 'united states of america'}
COUNTRY_ALIASES = {'usa': 'United States', 'us': 'United States', 'u.s.': 'United States',
                   'united states of america': 'United States', 'uk': 'United Kingdom',
                   'can': 'Canada'}
CITY_ALIASES = {
    'nyc': 'New York, NY', 'new york city': 'New York, NY', 'manhattan': 'New York, NY',
    'sf': 'San Francisco, CA', 'south sf': 'South San Francisco, CA',
    'la': 'Los Angeles, CA', 'dc': 'Washington, DC', 'washington dc': 'Washington, DC',
    'washington d.c.': 'Washington, DC',
}
REMOTE_REGIONS = {'': 'Remote', 'usa': 'Remote (US)', 'us': 'Remote (US)', 'united states': 'Remote (US)',
                  'canada': 'Remote (Canada)', 'uk': 'Remote (UK)', 'united kingdom': 'Remote (UK)'}
UNSPECIFIED = {'', 'unknown', 'not specified', 'multiple locations', 'various', 'tbd', 'n/a', 'na', 'anywhere'}

SEASON_ORDER = {'Winter': 0, 'Spring': 1, 'Summer': 2, 'Fall': 3}
TERM_PATTERN = re.compile(r"\b(Winter|Spring|Summer|Fall|Autumn)\s*(20\d{2})\b", re.I)


def _clean(text):
    text = unicodedata.normalize('NFC', text).replace('’', "'").replace(' ', ' ')
    return re.sub(r'\s+', ' ', text).strip(' ,.')


def _ascii_key(text):
    stripped = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return stripped.casefold()


def canonical_location(value):
    """One location fragment -> canonical label, or None when it carries no place."""
    text = _clean(value)
    key = text.casefold()
    if key in UNSPECIFIED:
        return None
    remote = re.fullmatch(r'remote(?:(?:\s*(?:in\b|[,\-–(])\s*|\s+)(.*?)\)?)?', text, re.I)
    if remote:
        region = (remote[1] or '').strip(' ,')
        return REMOTE_REGIONS.get(region.casefold(), 'Remote (%s)' % COUNTRY_ALIASES.get(region.casefold(), region))
    if key in CITY_ALIASES:
        return CITY_ALIASES[key]
    parts = [_clean(part) for part in text.split(',')]
    parts = [part for index, part in enumerate(parts) if part and (index == 0 or part.casefold() != parts[index - 1].casefold())]
    if not parts:
        return None
    # "Manhattan, NYC" and "South SF" style fragments end in a known metro alias.
    if parts[-1].casefold() in CITY_ALIASES:
        return CITY_ALIASES[parts[-1].casefold()]
    # A trailing country is redundant once a US state or Canadian province code is present.
    if len(parts) >= 3 and parts[-1].casefold() in NORTH_AMERICA and parts[-2].upper() in REGION_CODES:
        parts = parts[:-1]
    if len(parts) >= 2:
        region = parts[-1].upper() if parts[-1].upper() in REGION_CODES else REGION_BY_NAME.get(parts[-1].casefold())
        if region:
            # Neighborhood, City, ST keeps only City, ST so one city is one filter value.
            city = 'Quebec City' if _ascii_key(parts[-2]) == 'quebec city' else parts[-2]
            return '%s, %s' % (city, region)
        if len(parts) >= 3:
            parts = parts[-2:]
        country = COUNTRY_ALIASES.get(parts[-1].casefold())
        return ', '.join(parts[:-1] + [country or parts[-1]])
    single = parts[0]
    if single.upper() in REGION_CODES and len(single) == 2:
        return REGION_CODES[single.upper()]
    return COUNTRY_ALIASES.get(single.casefold(), REGION_CODES.get(REGION_BY_NAME.get(single.casefold()), single))


def canonical_locations(raw):
    """A raw location cell -> ordered, de-duplicated canonical locations."""
    seen = []
    for fragment in re.split(r'\s*(?:/|;|\||\n)\s*', raw or ''):
        label = canonical_location(fragment)
        if label and label not in seen:
            seen.append(label)
    return seen


def term_sort_key(term):
    season, year = term.split(' ')
    return int(year), SEASON_ORDER[season]


def canonical_terms(term=None, title=None):
    """Structured terms win; titles supply terms only when the term cell has none."""
    for text in (term, title):
        found = {'%s %s' % ('Fall' if season.casefold() == 'autumn' else season.title(), year)
                 for season, year in TERM_PATTERN.findall(text or '')}
        if found:
            return sorted(found, key=term_sort_key)
    return []
