import { useEffect, useState } from 'react';
import { getListings } from '../api/jobs';
import { getInteractionStates } from '../api/interactions';
import type { JobDetails, JobSource } from '../types/jobs';
import type { JobWorkflow } from '../hooks/useJobActions';
import { emptyInteraction } from '../lib/workflow';
import { emptyFilters, filterListings } from '../lib/listings';
import { FilterBar } from '../components/FilterBar';
import { JobActions } from '../components/JobActions';
import { SkillTag } from '../components/SkillTag';
import { SyncControl } from '../components/SyncControl';

export function ListingsPage({ workflow, sources, revision, onUpdated, onOpenJob }: {
  workflow: JobWorkflow; sources: JobSource[]; revision: number; onUpdated: () => void; onOpenJob: (id: number) => void;
}) {
  const [jobs, setJobs] = useState<JobDetails[]>([]);
  const [filters, setFilters] = useState(emptyFilters);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [limit, setLimit] = useState(40);
  const { install } = workflow;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false);
    Promise.all([getListings(controller.signal), getInteractionStates(controller.signal)]).then(([results, states]) => {
      if (!controller.signal.aborted) { setJobs(results); install(states); }
    }).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision, attempt, install]);
  const eligible = jobs.filter(item => !workflow.states[item.job.id]?.dismissed && !workflow.states[item.job.id]?.applied);
  const visible = filterListings(eligible, filters);
  return <main className="workspace listings-page" id="main-content">
    <div className="page-heading"><h1 tabIndex={-1}>All listings</h1><button id="reload" className="text-button" disabled={loading} onClick={() => setAttempt(value => value + 1)}>Refresh</button></div>
    <FilterBar jobs={eligible} sources={sources} filters={filters} onChange={value => { setFilters(value); setLimit(40); }} />
    <div className="directory-meta"><span id="summary">{loading ? 'Loading listings…' : `${visible.length} of ${eligible.length} jobs`}</span><SyncControl onUpdated={onUpdated} /></div>
    {loading ? <div role="status">{[0, 1, 2].map(value => <div className="skeleton-row" key={value} />)}</div>
      : error ? <div className="load-error" role="alert"><p>Could not load listings.</p><button className="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>
      : !visible.length ? <p className="empty-state">No listings match these filters.</p>
      : <><div className="listing-list" id="jobs">{visible.slice(0, limit).map(item => <article className="listing-row" data-job-id={item.job.id} key={item.job.id}>
        <div className="listing-title"><p className="job-company">{item.job.company}</p><h2><button className="job-title" onClick={() => onOpenJob(item.job.id)}>{item.job.title}</button></h2>
          <div className="skill-tags">{[...new Set([...item.keywords.languages, ...item.keywords.frameworks, ...item.keywords.tools_and_platforms])].slice(0, 3).map(skill => <SkillTag key={skill} label={skill} />)}</div></div>
        <p className="listing-location">{item.job.location}<span>{item.term || 'Term not specified'}</span>{item.job.status === 'unknown' && <span>Availability unconfirmed</span>}</p>
        <div className="listing-controls"><JobActions job={item.job} state={workflow.states[item.job.id] ?? emptyInteraction(item.job.id)}
          pending={workflow.pending[item.job.id]} error={workflow.errors[item.job.id]} onApply={workflow.apply}
          onSave={(id, type) => void workflow.act(id, type)} onApplied={(id, type) => void workflow.act(id, type)} onDismiss={(id, type) => void workflow.act(id, type)} /></div>
      </article>)}</div><div className="collection-footer"><span>Showing {Math.min(limit, visible.length)} of {visible.length}</span>
        {limit < visible.length && <button className="button" onClick={() => setLimit(value => value + 40)}>Show more</button>}</div></>}
  </main>;
}
