import type { AppliedAction, CandidateJob, InteractionState, InteractionType, JobView, RankingSignals, Recommendation, SaveAction } from '../types/jobs';
import { JobActions } from './JobActions';
import { SkillTag } from './SkillTag';
import { MatchSummary } from './MatchSummary';

interface Props {
  view?: JobView; recommendation: Recommendation & Partial<RankingSignals> & Partial<Pick<CandidateJob, 'term' | 'keywords'>>;
  interactionState: InteractionState; pending?: InteractionType; error?: string;
  onApply: (id: number) => void; onSave: (id: number, type: SaveAction) => void; onDismiss: (id: number) => void; onApplied: (id: number, type: AppliedAction) => void;
  onOpenJob?: (id: number) => void; onOpenProfile?: () => void;
}
export function RecommendationCard({ view = 'recommended', recommendation, interactionState, pending, error,
  onApply, onSave, onDismiss, onApplied, onOpenJob, onOpenProfile }: Props) {
  const { job, keywords } = recommendation;
  const personalized = view === 'recommended' && recommendation.final_score !== undefined;
  const skills = [...new Set(keywords ? [...keywords.languages, ...keywords.frameworks, ...keywords.tools_and_platforms] : recommendation.matched_skills)];
  return <article className={`recommendation-card${interactionState.applied ? ' is-applied' : ''}`} data-job-id={job.id} aria-labelledby={`job-title-${job.id}`}>
    <div className="recommendation-heading"><div className="job-information">
      <p className="job-company">{job.company}</p>
      <h2 id={`job-title-${job.id}`}><button className="job-title" onClick={() => onOpenJob?.(job.id)}>{job.title}</button></h2>
      <p className="job-meta"><span>{job.location}</span>{recommendation.term && <><span aria-hidden="true">·</span><span>{recommendation.term}</span></>}
        {['closed', 'dead'].includes(job.status) && <span className="job-availability">{job.status === 'closed' ? 'Posting closed' : 'Link unavailable'}</span>}</p>
      {view === 'applied' && interactionState.applied_at && <p className="applied-date">Applied <time dateTime={interactionState.applied_at}>
        {new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(interactionState.applied_at))}</time></p>}
    </div><span className="match-score">{personalized ? recommendation.final_score : recommendation.score}<small>% match</small></span></div>
    {skills.length > 0 && <div className="skill-tags matched-technologies" aria-label="Job skills">{skills.slice(0, 6).map(skill => <SkillTag key={skill} label={skill} />)}
      {skills.length > 6 && <span className="tag-overflow">+{skills.length - 6}</span>}</div>}
    <MatchSummary recommendation={recommendation} personalized={personalized} onOpenProfile={onOpenProfile} />
    <JobActions job={job} state={interactionState} pending={pending} error={error} appliedView={view === 'applied'}
      onApply={onApply} onSave={onSave} onApplied={onApplied} onDismiss={onDismiss} />
  </article>;
}
