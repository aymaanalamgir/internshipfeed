// Load and render scored job collections with shared workflow state and retry handling.
import { useEffect, useState } from 'react';
import { getAppliedJobs, getDismissedJobs, getRecommendations, getSavedJobs } from '../api/recommendations';
import { ApplicationStatusControl } from '../components/ApplicationStatusControl';
import { APPLICATION_STATUSES } from '../lib/applications';
import { RecommendationCard } from '../components/RecommendationCard';
import { emptyInteraction, visibleJobs } from '../lib/workflow';
import type { ApplicationStatus, CandidateJob, JobView } from '../types/jobs';
import type { JobWorkflow } from '../hooks/useJobActions';

const LOADERS = { recommended: getRecommendations, saved: getSavedJobs, applied: getAppliedJobs, dismissed: getDismissedJobs };
const TITLES = { recommended: 'Recommended', saved: 'Saved', applied: 'Applied', dismissed: 'Dismissed' };
const EMPTY = { recommended: 'No new recommendations.', saved: 'No saved jobs yet.', applied: 'No applications yet.', dismissed: 'No dismissed jobs.' };

export function JobCollectionPage({ view, workflow, revision, onOpenJob, onOpenProfile }: {
  view: JobView; workflow: JobWorkflow; revision: number; onOpenJob: (id: number) => void; onOpenProfile: () => void;
}) {
  const [applicationFilter, setApplicationFilter] = useState<ApplicationStatus | ''>('');
  const applicationRevision = view === 'applied' ? workflow.applicationRevision : 0;
  const [jobs, setJobs] = useState<CandidateJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [limit, setLimit] = useState(24);
  const { install } = workflow;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false);
    const request = view === 'applied' ? getAppliedJobs(controller.signal, applicationFilter || undefined) : LOADERS[view](controller.signal);
    request.then(results => {
      if (!controller.signal.aborted) { setJobs(results); install(results.map(item => item.interaction_state)); }
    }).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [view, revision, attempt, install, applicationFilter, applicationRevision]);
  const visible = visibleJobs(view, jobs, workflow.states) as CandidateJob[];
  return <main className="workspace recommendations-page" id="main-content">
    <div className="page-heading"><h1 tabIndex={-1}>{TITLES[view]}</h1><div className="collection-tools">
      {view === 'applied' && <label className="application-filter" htmlFor="application-filter">Status
        <select id="application-filter" value={applicationFilter} onChange={event => { setApplicationFilter(event.target.value as ApplicationStatus | ''); setLimit(24); }}>
          <option value="">All</option>{APPLICATION_STATUSES.map(([status, label]) => <option key={status} value={status}>{label}</option>)}
        </select>
      </label>}
      <span className="workspace-label">{loading ? 'Loading…' : `${visible.length} ${visible.length === 1 ? 'job' : 'jobs'}`}</span>
    </div></div>
    {loading ? <div role="status" className="loading-profile">Loading jobs…{[0, 1, 2].map(value => <div className="skeleton-row" key={value} />)}</div>
      : error ? <div className="load-error" role="alert"><p>Could not load {TITLES[view].toLowerCase()} jobs.</p><button className="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>
      : !visible.length ? <p className="empty-state">{view === 'applied' && applicationFilter ? 'No applications with this status.' : EMPTY[view]} {view === 'recommended' && <button className="text-button" onClick={onOpenProfile}>Edit profile</button>}</p>
      : <><div className="recommendation-list">{visible.slice(0, limit).map(item => <RecommendationCard key={item.job.id} recommendation={item} view={view}
        applicationControl={view === 'applied' && item.application && <ApplicationStatusControl key={item.application.id + ':' + item.application.updated_at}
          application={item.application} disabled={Boolean(workflow.pending[item.job.id])} onUpdated={() => setAttempt(value => value + 1)} />}
        interactionState={workflow.states[item.job.id] ?? emptyInteraction(item.job.id)} pending={workflow.pending[item.job.id]} error={workflow.errors[item.job.id]}
        onOpenJob={onOpenJob} onOpenProfile={onOpenProfile} onApply={workflow.apply} onSave={(id, type) => void workflow.act(id, type)}
        onApplied={(id, type) => void workflow.act(id, type)} onDismiss={(id, type) => void workflow.act(id, type)} />)}</div>
        <div className="collection-footer"><span>Showing {Math.min(limit, visible.length)} of {visible.length}</span>
          {limit < visible.length && <button className="button" onClick={() => setLimit(value => value + 24)}>Show more</button>}</div></>}
  </main>;
}
