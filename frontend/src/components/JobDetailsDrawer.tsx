// Load job descriptions, technical metadata, provenance, and shared actions in a drawer.
import { useEffect, useState } from 'react';
import { getJobDetails } from '../api/jobs';
import type { JobDetails, JobSource, JobView } from '../types/jobs';
import type { JobWorkflow } from '../hooks/useJobActions';
import { emptyInteraction, applicationUrl } from '../lib/workflow';
import { Drawer } from './Drawer';
import { SkillTag } from './SkillTag';
import { JobActions } from './JobActions';

export function JobDetailsDrawer({ id, onClose, workflow, sources, view }: { id: number; onClose: () => void; workflow: JobWorkflow; sources: JobSource[]; view: JobView | 'listings' }) {
  const [details, setDetails] = useState<JobDetails | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    getJobDetails(id, controller.signal).then(value => { if (!controller.signal.aborted) setDetails(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [id, attempt]);
  const job = details?.job;
  const provenance = details ? [...new Map(details.sources.map(source => [source.source_id, source])).values()] : [];
  return <Drawer kind="job" title={job?.title ?? 'Job'} onClose={onClose}>
    {error ? <div className="load-error" role="alert"><p>Could not load this job.</p><button className="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>
      : !details || !job ? <p role="status">Loading job…</p>
      : <><p className="detail-meta">{job.company} · {job.location}{details.term && ` · ${details.term}`}</p>
        {['closed', 'dead'].includes(job.status) && <p className="muted">{job.status === 'closed' ? 'Posting closed' : 'Application link unavailable'}</p>}
        <div className="detail-skills">{([['languages', 'Languages'], ['frameworks', 'Frameworks'], ['tools_and_platforms', 'Tools & platforms'], ['concepts', 'Concepts']] as const)
          .filter(([key]) => details.keywords[key].length > 0).map(([key, label]) => <div className="skill-group" key={key}>
            <h3>{label}</h3><div className="skill-tags">{[...new Set(details.keywords[key])].map(skill => <SkillTag key={skill} label={skill} />)}</div></div>)}</div>
        {details.keywords.requirements.length > 0 && <ul className="requirements">{details.keywords.requirements.map((value, index) => <li key={index}>{value}</li>)}</ul>}
        <div className="job-description">{details.description || 'A description is not available yet. Open the application for the full posting.'}</div>
        <JobActions job={job} state={workflow.states[job.id] ?? workflow.states[id] ?? emptyInteraction(job.id)}
          pending={workflow.pending[job.id]} error={workflow.errors[job.id]} appliedView={view === 'applied'}
          onApply={workflow.apply} onSave={(jobId, type) => void workflow.act(jobId, type)} onApplied={(jobId, type) => void workflow.act(jobId, type)}
          onDismiss={(jobId, type) => void workflow.act(jobId, type).then(success => { if (success) onClose(); })} />
        {provenance.length > 0 && <p className="provenance">Listed by {provenance.map((source, index) => <span key={source.source_id}>
          {index > 0 && ' · '}<a href={applicationUrl(source.source) ?? undefined} target="_blank" rel="noopener noreferrer">
            {sources.find(item => item.id === source.source_id)?.name ?? source.source.split('/').filter(Boolean).slice(-2).join(' / ')}</a></span>)}</p>}
      </>}
  </Drawer>;
}
