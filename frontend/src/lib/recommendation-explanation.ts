// Translate existing match evidence into concise explanations without recalculating scores.
import type { CandidateJob, RankingSignals, Recommendation } from '../types/jobs';
import type { ProfileValues } from '../types/profile';

export type ExplainedRecommendation = Recommendation & Partial<RankingSignals> & Partial<Pick<CandidateJob, 'term' | 'keywords'>>;
export interface MatchExplanation {
  hint: string;
  reasons: string[];
  behavior: string[];
  gaps: string[];
  emptyProfile: boolean;
  limitedMatch: boolean;
}

function evidence(reasons: string[], prefix: string) {
  const reason = reasons.find(value => value.startsWith(prefix));
  const value = reason?.slice(prefix.length).match(/^(.+) via "/)?.[1];
  return { reason, value };
}

function behaviorReason(reason: string): string | undefined {
  if (reason.startsWith("Similar to roles you've shown interest in:")) return "Similar to roles you've shown interest in";
  if (reason.startsWith("Shares skills with jobs you've shown interest in:")) {
    return reason.replace("Shares skills with jobs you've shown interest in:", "Matches skills you've shown interest in:").replace(/\.$/, '');
  }
  if (["Similar to companies you've shown interest in.", "Matches locations you've shown interest in.",
    "Matches terms you've shown interest in."].includes(reason)) return reason.replace(/\.$/, '');
}

const known = (value: string | null | undefined) => Boolean(value?.trim() && !/^(unknown|not specified|multiple locations|various|tbd|n\s*\/\s*a|anywhere)$/i.test(value.trim()));

export function explainRecommendation(recommendation: ExplainedRecommendation, personalized: boolean, profile?: ProfileValues): MatchExplanation {
  const emptyProfile = profile !== undefined && [profile.target_roles, profile.skills, profile.preferred_locations, profile.preferred_terms]
    .every(values => values.every(value => !value.trim()));
  const score = personalized ? recommendation.final_score ?? recommendation.score : recommendation.score;
  const limitedMatch = !emptyProfile && score < 40;
  const reasons: string[] = [], hints: string[] = [], gaps: string[] = [];
  const role = evidence(recommendation.reasons, 'Role matches ');
  const location = evidence(recommendation.reasons, 'Preferred location matches ');
  const term = evidence(recommendation.reasons, 'Preferred term matches ');
  const skills = [...new Set(recommendation.matched_skills)];

  if (recommendation.role_score > 0) {
    reasons.push(`${recommendation.role_score === 40 ? 'Matches' : 'Related to'} your target role${role.value ? `: ${role.value}` : ''}`);
    hints.push(role.value ?? 'Target role match');
  }
  if (skills.length) {
    reasons.push(`Matches your skills: ${skills.slice(0, 3).join(' · ')}${skills.length > 3 ? ` +${skills.length - 3} more` : ''}`);
    hints.push(skills.slice(0, 2).join(' + '));
  }
  if (recommendation.location_score > 0) {
    if (location.reason?.endsWith('(country overlap only)')) {
      reasons.push('In the same country as your preferred locations');
    } else if (location.reason?.endsWith('(province overlap)')) {
      reasons.push('In the same province as your preferred locations');
    } else {
      reasons.push(location.value ? `Matches your preferred location: ${location.value}` : 'Location overlaps with your preferences');
      hints.push(location.value ?? 'Preferred location');
    }
  }
  if (recommendation.term_score > 0) {
    if (term.reason?.endsWith('(season only; year missing)')) reasons.push('Matches your preferred season');
    else if (term.reason?.endsWith('(year only; season missing)')) reasons.push('Matches your preferred year');
    else {
      reasons.push(term.value ? `Fits ${term.value}` : 'Term overlaps with your preferences');
      if (term.value) hints.push(term.value);
    }
  }

  if (limitedMatch && profile) {
    if (profile.target_roles.length && recommendation.role_score === 0 && known(recommendation.job.title)) gaps.push('Role is outside your main targets');
    const keywords = recommendation.keywords;
    if (profile.skills.length && !skills.length && keywords && [keywords.languages, keywords.frameworks, keywords.tools_and_platforms].some(values => values.length)) {
      gaps.push('Few overlapping skills');
    }
    if (profile.preferred_locations.length && recommendation.location_score === 0 && known(recommendation.job.location)) gaps.push("Location isn't one of your preferences");
  }
  const behavior = personalized ? [...new Set((recommendation.behavior_reasons ?? []).map(behaviorReason).filter((value): value is string => Boolean(value)))].slice(0, 2) : [];
  return {
    hint: emptyProfile ? 'Personalization is still getting started.' : hints.slice(0, 2).join(' · ') || behavior[0] || (limitedMatch ? 'Limited match' : 'Explore this opportunity'),
    reasons, behavior, gaps: gaps.slice(0, 2), emptyProfile, limitedMatch,
  };
}
