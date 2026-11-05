// Show compact, evidence-based reasons for a recommendation with optional profile guidance.
import { useState } from 'react';
import { explainRecommendation } from '../lib/recommendation-explanation';
import type { ExplainedRecommendation, MatchExplanation } from '../lib/recommendation-explanation';
import type { ProfileValues } from '../types/profile';

export function MatchExplanationContent({ explanation, onOpenProfile }: { explanation: MatchExplanation; onOpenProfile?: () => void }) {
  return <>
    {explanation.emptyProfile ? <><p>Personalization is still getting started.</p>
      <p>Add your target roles, skills, locations, and internship terms to improve recommendations.</p>
      {onOpenProfile && <button className="text-button" type="button" onClick={onOpenProfile}>Edit profile</button>}</>
      : <>{explanation.limitedMatch && <p className="match-label">Limited match</p>}
        {explanation.reasons.length > 0 && <ul className="match-reasons">{explanation.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
        {explanation.gaps.length > 0 && <ul className="match-gaps">{explanation.gaps.map(reason => <li key={reason}>{reason}</li>)}</ul>}
        {!explanation.reasons.length && !explanation.gaps.length && !explanation.behavior.length && <p>No strong matches in the available job details yet.</p>}</>}
    {explanation.behavior.length > 0 && <ul className="behavior-reasons">{explanation.behavior.map(reason => <li key={reason}>{reason}</li>)}</ul>}
  </>;
}

export function MatchSummary({ recommendation, personalized, profile, onOpenProfile }: {
  recommendation: ExplainedRecommendation; personalized: boolean; profile?: ProfileValues; onOpenProfile?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const explanation = explainRecommendation(recommendation, personalized, profile);
  return <div className="match-summary">
    {!expanded && <p className="match-hint">{explanation.hint}</p>}
    <button className="text-button match-toggle" type="button" aria-expanded={expanded} aria-controls={`match-${recommendation.job.id}`}
      aria-label={`Why this job? ${recommendation.job.title}`} onClick={() => setExpanded(value => !value)}>
      Why this job? <span aria-hidden="true">{expanded ? '−' : '+'}</span></button>
    {expanded && <div className="match-explanation" id={`match-${recommendation.job.id}`}>
      <MatchExplanationContent explanation={explanation} onOpenProfile={onOpenProfile} />
    </div>}
  </div>;
}
