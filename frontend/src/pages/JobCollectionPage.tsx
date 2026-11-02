import { useEffect, useRef, useState } from 'react';
import { recordInteraction } from '../api/interactions';
import { getAppliedJobs, getRecommendations, getSavedJobs } from '../api/recommendations';
import { RecommendationCard } from '../components/RecommendationCard';
import { confirmInteraction, emptyInteraction, indexInteractions, visibleJobs } from '../lib/workflow';
import type { CandidateJob, InteractionStates, InteractionType, JobView } from '../types/jobs';

const LOADERS = { recommended: getRecommendations, saved: getSavedJobs, applied: getAppliedJobs };
const TITLES = { recommended: 'Recommended', saved: 'Saved', applied: 'Applied' };
const EMPTY = { recommended: 'No new recommendations. Saved and applied jobs are available in their own views.',
  saved: 'No saved jobs yet.', applied: 'No applications recorded yet.' };

export function JobCollectionPage({ view }: { view: JobView }) {
  const [recommendations, setRecommendations] = useState<CandidateJob[]>([]);
  const [states, setStates] = useState<InteractionStates>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [pending, setPending] = useState<Record<number, InteractionType | undefined>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [announcement, setAnnouncement] = useState('');
  const [limit, setLimit] = useState(24);
  const busy = useRef(new Set<number>());

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError(false);
    // Collections arrive with their SQL-derived product state in one response.
    LOADERS[view](controller.signal)
      .then(jobs => {
        if (!controller.signal.aborted) { setRecommendations(jobs); setStates(indexInteractions(jobs.map(job => job.interaction_state))); }
      })
      .catch(() => { if (!controller.signal.aborted) setLoadError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [view, attempt]);

  async function act(jobId: number, type: 'save' | 'dismiss' | 'applied') {
    if (busy.current.has(jobId)) return;
    busy.current.add(jobId);
    setPending(current => ({ ...current, [jobId]: type }));
    setErrors(current => ({ ...current, [jobId]: '' }));
    try {
      const event = await recordInteraction(jobId, type);
      setStates(current => confirmInteraction(current, jobId, event));
      setAnnouncement(type === 'dismiss' ? 'Job dismissed.' : type === 'save' ? 'Job saved.' : 'Marked as applied.');
    } catch {
      const message = type === 'save' ? 'Could not save this job.' : type === 'dismiss' ? 'Could not dismiss this job.' : 'Could not mark this job as applied.';
      setErrors(current => ({ ...current, [jobId]: `${message} Please try again.` }));
    } finally {
      busy.current.delete(jobId);
      setPending(current => ({ ...current, [jobId]: undefined }));
    }
  }

  function apply(jobId: number) {
    // A native target=_blank link handles navigation synchronously. Never cancel it.
    setErrors(current => ({ ...current, [jobId]: '' }));
    void recordInteraction(jobId, 'click')
      .then(event => setStates(current => confirmInteraction(current, jobId, event)))
      .catch(() => setErrors(current => ({ ...current, [jobId]: 'Click could not be recorded. The application link is still available.' })));
  }

  const visible = visibleJobs(view, recommendations, states);
  return (
    <main className="recommendations-page" id="main-content">
      <div className="page-heading"><h1>{TITLES[view]}</h1><span className="workspace-label"><span />{loading ? 'Loading jobs' : `${visible.length} ${visible.length === 1 ? 'job' : 'jobs'}`}</span></div>
      <div className="recommendations-intro"><p>{view === 'recommended' ? <>Based on your <a href="/profile">profile preferences</a>.</>
        : view === 'saved' ? 'Jobs you bookmarked.' : 'Your application history.'}</p><span>Role 40 · Skills 30 · Location 20 · Term 10</span></div>
      <p className="workflow-notice" role="status">{announcement}</p>
      {loading ? <div className="loading-profile" role="status" aria-label={`Loading ${TITLES[view].toLowerCase()} jobs`}>
        <span>Loading {TITLES[view].toLowerCase()} jobs…</span>{[0, 1, 2].map(value => <div className="skeleton-field" key={value} />)}
      </div> : loadError ? <div className="load-error" role="alert"><p>Could not load {TITLES[view].toLowerCase()} jobs.</p>
        <button className="button button-secondary" onClick={() => setAttempt(value => value + 1)}>Try again</button>
      </div> : !visible.length ? <p className="recommendations-empty">{EMPTY[view]}</p>
      : <>
        <div className="recommendation-list">
          {visible.slice(0, limit).map(recommendation => <RecommendationCard key={recommendation.job.id} recommendation={recommendation}
            view={view}
            interactionState={states[recommendation.job.id] ?? emptyInteraction(recommendation.job.id)}
            pending={pending[recommendation.job.id]} error={errors[recommendation.job.id]}
            onApply={apply} onSave={id => void act(id, 'save')} onDismiss={id => void act(id, 'dismiss')} onApplied={id => void act(id, 'applied')} />)}
        </div>
        <div className="recommendations-more"><span>Showing {Math.min(limit, visible.length)} of {visible.length}</span>
          {limit < visible.length && <button className="button button-secondary" onClick={() => setLimit(value => value + 24)}>Show more</button>}
        </div>
      </>}
    </main>
  );
}
