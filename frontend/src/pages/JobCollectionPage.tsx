// Load and render scored job collections with shared workflow state and retry handling.
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { collectionQuery, invalidateJobs, profileQuery } from '../lib/queries';
import { LoadingState, RefreshState } from '../components/LoadingState';
import { ApplicationStatusControl } from '../components/ApplicationStatusControl';
import { APPLICATION_STATUSES } from '../lib/applications';
import { RecommendationCard } from '../components/RecommendationCard';
import { mergeInteractionStates, emptyInteraction, visibleJobs } from '../lib/workflow';
import type { ApplicationStatus, CandidateJob, JobView } from '../types/jobs';
import type { JobWorkflow } from '../hooks/useJobActions';

const TITLES = { recommended: 'Recommended', saved: 'Saved', applied: 'Applied', dismissed: 'Dismissed' };
const EMPTY = { recommended: 'No new recommendations.', saved: 'No saved jobs yet.', applied: 'No applications yet.', dismissed: 'No dismissed jobs.' };

export function JobCollectionPage({ view, workflow, onOpenJob, onOpenProfile }: {
  view: JobView; workflow: JobWorkflow; onOpenJob: (id: number) => void; onOpenProfile: () => void;
}) {
  const [applicationFilter, setApplicationFilter] = useState<ApplicationStatus | ''>('');
  const client = useQueryClient();
  const query = useQuery(collectionQuery(view, applicationFilter));
  const { data: profile } = useQuery(profileQuery());
  const jobs = query.data ?? [];
  const loading = query.data === undefined;
  const [limit, setLimit] = useState(24);
  const { install } = workflow;
  useEffect(() => { if (query.data) install(query.data.map(item => item.interaction_state)); }, [query.data, install]);
  const states = mergeInteractionStates(workflow.states, jobs.map(item => item.interaction_state));
  const visible = visibleJobs(view, jobs, states) as CandidateJob[];
  return <main className="workspace recommendations-page" id="main-content">
    <div className="page-heading"><h1 tabIndex={-1}>{TITLES[view]}</h1><div className="collection-tools">
      {view === 'applied' && <label className="application-filter" htmlFor="application-filter">Status
        <select id="application-filter" value={applicationFilter} onChange={event => { setApplicationFilter(event.target.value as ApplicationStatus | ''); setLimit(24); }}>
          <option value="">All</option>{APPLICATION_STATUSES.map(([status, label]) => <option key={status} value={status}>{label}</option>)}
        </select>
      </label>}
      <span className="workspace-label">{loading ? 'Loading…' : `${visible.length} ${visible.length === 1 ? 'job' : 'jobs'}`}</span>
    </div></div>
    {!loading && <RefreshState fetching={query.isFetching} error={query.isError} onRetry={() => void query.refetch()} />}
    {loading && !query.isError ? <LoadingState />
      : loading && query.isError ? <div className="load-error" role="alert"><p>Could not load {TITLES[view].toLowerCase()} jobs.</p><button className="button" onClick={() => void query.refetch()}>Try again</button></div>
      : !visible.length ? <p className="empty-state">{view === 'applied' && applicationFilter ? 'No applications with this status.' : EMPTY[view]} {view === 'recommended' && <button className="text-button" onClick={onOpenProfile}>Edit profile</button>}</p>
      : <><div className="recommendation-list">{visible.slice(0, limit).map(item => <RecommendationCard key={item.job.id} recommendation={item} view={view} profile={profile}
        applicationControl={view === 'applied' && item.application && <ApplicationStatusControl key={item.application.id + ':' + item.application.updated_at}
          application={item.application} disabled={Boolean(workflow.pending[item.job.id])} onUpdated={() => { void invalidateJobs(client); }} />}
        interactionState={states[item.job.id] ?? emptyInteraction(item.job.id)} pending={workflow.pending[item.job.id]} error={workflow.errors[item.job.id]}
        onOpenJob={onOpenJob} onOpenProfile={onOpenProfile} onApply={workflow.apply} onSave={(id, type) => void workflow.act(id, type)}
        onApplied={(id, type) => void workflow.act(id, type)} onDismiss={(id, type) => void workflow.act(id, type)} />)}</div>
        <div className="collection-footer"><span>Showing {Math.min(limit, visible.length)} of {visible.length}</span>
          {limit < visible.length && <button className="button" onClick={() => setLimit(value => value + 24)}>Show more</button>}</div></>}
  </main>;
}
