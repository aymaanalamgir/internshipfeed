"""Bounded, deterministic preferences derived on request from interaction history."""
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, FrozenSet, Iterable, List, Set

from .matching import location_keys, normalize_text, role_families, score_location, technical_skills, term_keys
from .models import JobDetails

INTERACTION_WEIGHTS = {'click': 1, 'save': 2, 'applied': 4, 'dismiss': -3}
DIMENSION_WEIGHTS = {'roles': 4, 'skills': 3, 'companies': 0.5, 'locations': 1, 'terms': 0.5}
AFFINITY_BOUND = 10
NEUTRAL_SCORE = 50


@dataclass(frozen=True)
class BehaviorEvidence:
    details: JobDetails
    interaction_types: FrozenSet[str]


@dataclass
class BehaviorProfile:
    roles: Dict[str, int] = field(default_factory=dict)
    skills: Dict[str, int] = field(default_factory=dict)
    companies: Dict[str, int] = field(default_factory=dict)
    locations: Dict[str, int] = field(default_factory=dict)
    terms: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class BehaviorScore:
    score: int
    contributions: Dict[str, float]
    reasons: List[str]


def job_features(details: JobDetails) -> Dict[str, Set[str]]:
    # Prefer current source observations. Closed history can still supply metadata.
    sources = [source for source in details.sources if source.status not in {'closed', 'dead'}] or details.sources
    titles = [details.job.title] + [source.title for source in sources]
    terms = [details.term or details.job.title] + [source.term or source.title for source in sources]
    company = normalize_text(details.job.company)
    return {
        'roles': role_families(titles),
        'skills': set(technical_skills(details.keywords)),
        'companies': {company} if company not in {'', 'unknown', 'not specified'} else set(),
        'locations': location_keys([details.job.location] + [source.location for source in sources]),
        'terms': term_keys(terms),
    }


def build_behavior_profile(history: Iterable[BehaviorEvidence]) -> BehaviorProfile:
    """One signal/type/current canonical job; clamp only after complete aggregation."""
    jobs, actions = {}, {}
    for evidence in history:
        job_id = evidence.details.job.id
        jobs.setdefault(job_id, evidence.details)
        actions.setdefault(job_id, set()).update(evidence.interaction_types)
    profile = BehaviorProfile()
    for job_id in sorted(jobs):
        weight = sum(INTERACTION_WEIGHTS.get(action, 0) for action in actions[job_id])
        if not weight:
            continue
        for dimension, keys in job_features(jobs[job_id]).items():
            affinities = getattr(profile, dimension)
            for key in sorted(keys):
                affinities[key] = affinities.get(key, 0) + weight
    for dimension in DIMENSION_WEIGHTS:
        affinities = getattr(profile, dimension)
        setattr(profile, dimension, {key: max(-AFFINITY_BOUND, min(AFFINITY_BOUND, value))
                                     for key, value in sorted(affinities.items()) if value})
    return profile


def _mean(values) -> Fraction:
    values = list(values)
    return Fraction(sum(values), len(values)) if values else Fraction(0)


def score_behavior(profile: BehaviorProfile, details: JobDetails) -> BehaviorScore:
    features = job_features(details)
    contributions = {}
    for dimension, multiplier in DIMENSION_WEIGHTS.items():
        affinities, keys = getattr(profile, dimension), features[dimension]
        if dimension == 'locations':
            # Only existing full city/region/remote matches count. Country/province
            # overlap alone must not spread one city's preference to other cities.
            alignment = _mean(_mean(affinity for key, affinity in affinities.items()
                                    if score_location([key], [location])[0] == 20)
                              for location in sorted(keys))
        else:
            alignment = _mean(affinities.get(key, 0) for key in sorted(keys))
        alignment = max(-AFFINITY_BOUND, min(AFFINITY_BOUND, alignment))
        contributions[dimension] = Fraction(multiplier) * alignment
    # Constant denominator: missing dimensions stay neutral instead of inflating
    # sparse metadata. All-max affinities map to 100, all-min to 0.
    maximum = AFFINITY_BOUND * sum(Fraction(weight) for weight in DIMENSION_WEIGHTS.values())
    value = NEUTRAL_SCORE + Fraction(50) * sum(contributions.values()) / maximum
    rounded = (2 * value.numerator + value.denominator) // (2 * value.denominator)
    labels = technical_skills(details.keywords)
    positive_roles = [key.title() for key in sorted(features['roles']) if profile.roles.get(key, 0) > 0]
    positive_skills = [labels[key] for key in sorted(features['skills']) if profile.skills.get(key, 0) > 0]
    messages = {
        'roles': "Similar to roles you've shown interest in: " + ', '.join(positive_roles[:2]) + '.',
        'skills': "Shares skills with jobs you've shown interest in: " + ', '.join(positive_skills[:3]) + '.',
        'companies': "Similar to companies you've shown interest in.",
        'locations': "Matches locations you've shown interest in.",
        'terms': "Matches terms you've shown interest in.",
    }
    positive = sorted((key for key in contributions if contributions[key] > 0),
                      key=lambda key: (-contributions[key], list(DIMENSION_WEIGHTS).index(key)))
    return BehaviorScore(max(0, min(100, rounded)), {key: float(value) for key, value in contributions.items()},
                         [messages[key] for key in positive[:2]])
