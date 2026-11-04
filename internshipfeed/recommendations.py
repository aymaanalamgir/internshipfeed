"""Compose independent profile/behavior signals after workflow filtering."""
from typing import Iterable, List

from .behavioral import BehaviorProfile, score_behavior
from .matching import rank_jobs
from .models import CandidateProfile, JobDetails, RankedMatchResult


def rank_recommendations(profile: CandidateProfile, jobs: Iterable[JobDetails],
                         behavior: BehaviorProfile, min_score: int = 0) -> List[RankedMatchResult]:
    details = {item.job.id: item for item in jobs}
    results = []
    # Keep min_score's existing meaning: minimum explicit profile fit.
    for match in rank_jobs(profile, details.values(), min_score=min_score):
        preference = score_behavior(behavior, details[match.job.id])
        results.append(RankedMatchResult(**match.model_dump(), profile_score=match.score,
                       behavior_score=preference.score, final_score=(3 * match.score + preference.score + 2) // 4,
                       behavior_reasons=preference.reasons))
    results.sort(key=lambda result: (result.final_score, result.job.posted_at is not None,
                                    result.job.posted_at.toordinal() if result.job.posted_at else 0,
                                    result.job.id), reverse=True)
    return results
