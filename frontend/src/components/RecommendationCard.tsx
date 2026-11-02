import { useState } from 'react';
import type { InteractionState, InteractionType, JobView, Recommendation } from '../types/jobs';
import { applicationUrl } from '../lib/workflow';

function ActionIcon({ kind, filled = false }: { kind: 'bookmark' | 'clock' | 'close' | 'external'; filled?: boolean }) {
  const paths = {
    bookmark: 'M5 3h14v18l-7-4-7 4V3Z',
    clock: 'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
    close: 'm6 6 12 12M6 18 18 6',
    external: 'M14 3h7v7M21 3 10 14M10 3H3v18h18v-7',
  };
  return <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill={filled ? 'currentColor' : 'none'}>
    <path d={paths[kind]} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

interface Props {
  view?: JobView;
  recommendation: Recommendation;
  interactionState: InteractionState;
  pending?: InteractionType;
  error?: string;
  onApply: (id: number) => void;
  onSave: (id: number) => void;
  onDismiss: (id: number) => void;
  onApplied: (id: number) => void;
}

export function RecommendationCard({ view = 'recommended', recommendation, interactionState, pending, error, onApply, onSave, onDismiss, onApplied }: Props) {
  const [expanded, setExpanded] = useState(false);
  const { job, matched_skills: skills } = recommendation;
  const url = applicationUrl(job.application_url);
  const busy = Boolean(pending);

  return (
    <article className={`recommendation-card${interactionState.applied ? ' is-applied' : ''}`} data-job-id={job.id}
      aria-labelledby={`job-title-${job.id}`}>
      <div className="recommendation-heading">
        <div className="job-information">
          <p className="job-company">{job.company}</p>
          <h2 id={`job-title-${job.id}`}>{job.title}</h2>
          <p className="job-meta"><span>{job.location}</span>{job.posted_at && <><span aria-hidden="true">·</span><time dateTime={job.posted_at}>Posted {job.posted_at}</time></>}</p>
          {view === 'applied' && interactionState.applied_at && <p className="applied-date">Applied <time dateTime={interactionState.applied_at}>
            {new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(interactionState.applied_at))}
          </time></p>}
          {['closed', 'dead'].includes(job.status) && <p className="job-availability">{job.status === 'closed' ? 'Posting closed' : 'Application link unavailable'}</p>}
        </div>
        <button className="match-toggle" type="button" aria-expanded={expanded} aria-controls={`match-${job.id}`}
          aria-label={`Why this score for ${job.title}`} onClick={() => setExpanded(value => !value)}>
          <span className="match-score">{recommendation.score}<small> / 100</small></span>
          <span className="match-caption">match <span aria-hidden="true">{expanded ? '−' : '+'}</span></span>
        </button>
      </div>
      {skills.length > 0 && <p className="matched-technologies" aria-label="Matched skills">
        {skills.slice(0, 4).join(' · ')}{skills.length > 4 ? ` · +${skills.length - 4} more` : ''}
      </p>}
      {expanded && <div className="match-explanation" id={`match-${job.id}`}>
        <dl className="score-breakdown">
          {([['Role', recommendation.role_score, 40], ['Skills', recommendation.skills_score, 30],
            ['Location', recommendation.location_score, 20], ['Term', recommendation.term_score, 10]] as const)
            .map(([label, points, maximum]) => <div key={label}><dt>{label}</dt><dd>{points} / {maximum}</dd></div>)}
        </dl>
        {recommendation.reasons.length ? <ul>{recommendation.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
          : <p>No matching preferences detected. You can edit your <a href="/profile">profile</a>.</p>}
      </div>}
      <div className="recommendation-actions">
        {url ? <a className="button apply-action" href={url} target="_blank" rel="noopener noreferrer" onClick={() => onApply(job.id)}>
          {view === 'applied' ? 'Open application' : 'Apply'}<ActionIcon kind="external" />
        </a> : <button className="button apply-action" type="button" disabled title="Application link unavailable">Apply unavailable</button>}
        <button className={`button utility-action${interactionState.saved ? ' is-active' : ''}`} type="button"
          aria-label={interactionState.saved ? 'Job saved' : 'Save job'} aria-pressed={interactionState.saved}
          disabled={busy || interactionState.saved} onClick={() => onSave(job.id)}>
          <ActionIcon kind="bookmark" filled={interactionState.saved} />{pending === 'save' ? 'Saving…' : interactionState.saved ? 'Saved' : 'Save'}
        </button>
        <button className={`button utility-action${interactionState.applied ? ' is-active' : ''}`} type="button"
          aria-label={interactionState.applied ? 'Job marked as applied' : 'Mark job as applied'} aria-pressed={interactionState.applied}
          disabled={busy || interactionState.applied} onClick={() => onApplied(job.id)}>
          <ActionIcon kind="clock" />{pending === 'applied' ? 'Recording…' : interactionState.applied ? 'Applied' : 'Mark applied'}
        </button>
        {view !== 'applied' && <button className="dismiss-action" type="button" aria-label="Dismiss job" title="Dismiss job"
          disabled={busy} onClick={() => onDismiss(job.id)}><ActionIcon kind="close" /></button>}
      </div>
      {error && <p className="field-error action-error" role="alert">{error}</p>}
    </article>
  );
}
