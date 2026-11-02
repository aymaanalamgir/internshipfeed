import { useState } from 'react';
import type { RankingSignals, Recommendation } from '../types/jobs';

export function MatchSummary({ recommendation, personalized, onOpenProfile }: {
  recommendation: Recommendation & Partial<RankingSignals>; personalized: boolean; onOpenProfile?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const behavior = personalized ? recommendation.behavior_reasons?.slice(0, 2) ?? [] : [];
  // Shorten the server's evidence copy; the points and matching decisions stay server-side.
  const fitHint = recommendation.reasons[0]?.split(' via ')[0];
  return <div className="match-summary">
    {!expanded && <>{fitHint && <p className="match-hint">{fitHint}</p>}{behavior[0] && <p className="match-hint">{behavior[0]}</p>}</>}
    <button className="text-button match-toggle" type="button" aria-expanded={expanded} aria-controls={`match-${recommendation.job.id}`}
      aria-label={`Why this score for ${recommendation.job.title}`} onClick={() => setExpanded(value => !value)}>
      Why this job? <span aria-hidden="true">{expanded ? '−' : '+'}</span></button>
    {expanded && <div className="match-explanation" id={`match-${recommendation.job.id}`}>
      {personalized && <><dl className="ranking-breakdown">
        <div><dt>Profile fit</dt><dd>{recommendation.profile_score} / 100</dd></div>
        <div><dt>Preference fit</dt><dd>{recommendation.behavior_score} / 100</dd></div>
        <div><dt>Final rank</dt><dd>{recommendation.final_score} / 100</dd></div>
      </dl><p className="ranking-formula">75% profile + 25% preferences. Preference fit starts at 50.</p>
        {behavior.length > 0 && <ul className="behavior-reasons">{behavior.map(reason => <li key={reason}>{reason}</li>)}</ul>}</>}
      <dl className="score-breakdown">{([['Role', recommendation.role_score, 40], ['Skills', recommendation.skills_score, 30],
        ['Location', recommendation.location_score, 20], ['Term', recommendation.term_score, 10]] as const)
        .map(([label, points, maximum]) => <div key={label}><dt>{label}</dt><dd>{points} / {maximum}</dd></div>)}</dl>
      {recommendation.reasons.length > 0 ? <ul>{recommendation.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
        : <p>No matching preferences yet. <button className="text-button" onClick={onOpenProfile}>Edit profile</button></p>}
    </div>}
  </div>;
}
